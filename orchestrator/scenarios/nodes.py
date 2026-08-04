"""
nodes.py — LangGraph node functions for each pipeline stage.

Each node function has the signature:
    (state: OrchestratorState) -> dict

LangGraph merges the returned dict into the shared OrchestratorState after
the node completes.  Nodes return ONLY the keys they changed — not the full
state — so concurrent fan-out nodes don't overwrite each other's output.

Gate nodes
----------
Gate nodes call LangGraph's interrupt() to pause execution and hand control
back to the CLI.  The human uses the CLI `approve` command to resume the
graph (Phase 17).  interrupt() serialises the pending state to PostgresSaver
so the process can exit safely and resume later with graph.invoke(None, config).

StageAgent dependency injection
--------------------------------
All stage-execution nodes are produced by make_stage_node() — a factory that
closes over a pre-built StageAgent, MemoryStore, RunStateStore, and AuditLogger.
This keeps the nodes as thin wrappers (the real logic is in StageAgent) and
makes them easily testable by passing mocks to the factory.

State merging
-------------
stage_artifacts accumulates across nodes using dict spread:
    return {"stage_artifacts": {**state.get("stage_artifacts", {}), "architecture_design": artifact}}

This is safe for fan-out because LangGraph merges returned dicts at the node
boundary — concurrent nodes writing different keys don't race.
"""

import logging
import time
from typing import Any, Callable

from langgraph.types import interrupt

from orchestrator.core.stage import StageResult, StageStatus
from orchestrator.core.state import OrchestratorState
from orchestrator.governance.audit import AuditLogger, EventType
from orchestrator.logging import PipelineLogger


def make_stage_node(
    stage_name: str,
    gateway: Any,            # AIGateway
    registry: Any,           # ToolRegistry
    session_factory: Any,    # Callable[[], Session] — called fresh per invocation
    actor: str = "system",
    actor_role: str = "DEVELOPER",
) -> Callable[[OrchestratorState], dict]:
    """Factory: returns a LangGraph node function for stage_name.

    Each invocation creates its own SQLAlchemy session so concurrent fan-out
    nodes (e.g. implementation_plan + test_plan) don't share a session across
    threads — SQLAlchemy sessions are not thread-safe.

    The returned function:
      1. Creates a fresh session + StageAgent + MemoryStore + RunStateStore
      2. Logs STAGE_STARTED to audit
      3. Runs StageAgent.run() — multi-turn LLM + tool loop
      4. Persists StageResult to orch_stage_results via RunStateStore
      5. Logs STAGE_COMPLETED or STAGE_FAILED to audit
      6. Returns dict with updated stage_artifacts
    """
    def node(state: OrchestratorState) -> dict:
        # Import here to avoid circular imports at module load time
        from orchestrator.agents.stage_agent import StageAgent
        from orchestrator.cache.response_cache import ResponseCache
        from orchestrator.gateway.cost_tracker import CostTracker
        from orchestrator.governance.audit import AuditLogger
        from orchestrator.memory.store import MemoryStore
        from orchestrator.state.store import RunStateStore

        run_id = state["run_id"]
        log = PipelineLogger(run_id=run_id, actor=actor, role=actor_role)
        t0 = time.perf_counter()
        log.stage_started(stage_name)

        session = session_factory()
        try:
            cache        = ResponseCache(session)
            memory_store = MemoryStore(session)
            run_store    = RunStateStore(session)
            audit        = AuditLogger(session)
            agent        = StageAgent(gateway, registry, cache)

            _audit(audit, run_id, EventType.STAGE_STARTED, details={"stage": stage_name})

            result: StageResult = agent.run(stage_name, state, memory_store, pipeline_logger=log)
            run_store.save_stage_result(result, run_id)

            if result.cache_hit:
                import uuid
                CostTracker().record(
                    session=session,
                    trace_id=str(uuid.uuid4()),
                    run_id=run_id,
                    stage_name=stage_name,
                    model=result.model_used or "gpt-4o",
                    prompt_version=result.prompt_version or "",
                    usage={"input_tokens": 0, "output_tokens": 0},
                    llm_latency_ms=0.0,
                    github_login=state.get("triggered_by", "unknown"),
                    cache_hit=True,
                )

            if result.status == StageStatus.FAILED:
                duration_ms = (time.perf_counter() - t0) * 1000
                log.stage_failed(stage_name, result.error_message or "stage failed", duration_ms)
                _audit(audit, run_id, EventType.STAGE_FAILED,
                       stage_name=stage_name, details={"error": result.error_message})
                return {"stage_artifacts": {
                    **state.get("stage_artifacts", {}),
                    stage_name: {"error": result.error_message, "status": "failed"},
                }}

            _audit(audit, run_id, EventType.STAGE_COMPLETED, stage_name=stage_name)

            # Collect stage-specific completion fields for the log event
            extra: dict = {}
            if stage_name in ("unit_tests", "integration_tests") and result.output_artifact:
                extra["passed"] = result.output_artifact.get("passed", 0)
                extra["failed"] = result.output_artifact.get("failed", 0)
            elif stage_name == "implementation" and result.output_artifact:
                for _k in ("branch_name", "pr_url", "pr_number"):
                    _v = result.output_artifact.get(_k)
                    if _v is not None:
                        extra[_k] = _v
            elif stage_name == "release_readiness" and result.output_artifact:
                _rts = result.output_artifact.get("ready_to_ship")
                if _rts is not None:
                    extra["ready_to_ship"] = _rts
            log.stage_completed(stage_name, (time.perf_counter() - t0) * 1000, **extra)

            # For implementation: if the LLM did not call commit_and_push / create_pr,
            # do it here as a fallback so a PR is always created.
            if stage_name == "implementation" and result.output_artifact:
                artifact = result.output_artifact
                branch = artifact.get("branch_name")

                # Recovery: LLM sometimes calls create_pr (tool succeeds) but then
                # returns null in the final JSON. Scan the tool_cache — which holds
                # every tool result from this stage — for a create_pr response that
                # contains pr_url. If found, patch the artifact instead of re-calling.
                if not artifact.get("pr_url"):
                    tool_cache_data = state.get("tool_cache", {})
                    for cached_val in tool_cache_data.values():
                        if (
                            isinstance(cached_val, dict)
                            and cached_val.get("pr_url")
                            and isinstance(cached_val["pr_url"], str)
                            and "github.com" in cached_val["pr_url"]
                        ):
                            artifact["pr_url"] = cached_val["pr_url"]
                            artifact["pr_number"] = cached_val.get("pr_number")
                            _persist_pr(
                                session_factory, run_id,
                                cached_val["pr_url"],
                                branch or "",
                            )
                            log.pr_created(
                                branch or "",
                                cached_val.get("pr_number", 0),
                                cached_val["pr_url"],
                            )
                            break

                if branch and not artifact.get("pr_url"):
                    log.tool_called("implementation", "commit_and_push", f"branch={branch}")
                    from orchestrator.tools import github_client
                    req = state.get("requirement", "")
                    try:
                        commit_result = github_client.commit_and_push(
                            branch,
                            f"feat: {req[:60]}" if req else "feat: orchestrator implementation",
                        )
                        log.tool_completed("implementation", "commit_and_push", commit_result, 0)
                        log.tool_called("implementation", "create_pr", f"branch={branch}")
                        pr_result = github_client.create_pr(
                            title=f"feat: {req[:70]}" if req else "feat: orchestrator implementation",
                            body=f"Automated PR from orchestrator run `{run_id}`.\n\n**Requirement**: {req}",
                            branch=branch,
                            base="main",
                        )
                        pr_number = pr_result["pr_number"]
                        pr_url = pr_result["pr_url"]
                        log.pr_created(branch, pr_number, pr_url)
                        artifact["pr_url"] = pr_url
                        artifact["pr_number"] = pr_number
                        # Persist to orch_runs so `status` command shows the PR
                        _persist_pr(session_factory, run_id, pr_url, branch)
                    except Exception as exc:
                        log.pr_error(branch, str(exc))
                        artifact["pr_url"] = None
                        artifact["pr_creation_error"] = str(exc)

            # If LLM itself set pr_url, persist it to orch_runs too
            if stage_name == "implementation" and result.output_artifact:
                artifact = result.output_artifact
                lm_pr_url = artifact.get("pr_url")
                lm_branch = artifact.get("branch_name")
                if lm_pr_url and lm_branch:
                    _persist_pr(session_factory, run_id, lm_pr_url, lm_branch)

            return {"stage_artifacts": {
                **state.get("stage_artifacts", {}),
                stage_name: result.output_artifact,
            }}
        finally:
            session.close()

    node.__name__ = f"{stage_name}_node"
    return node


def make_gate_node(
    gate_name: str,
    required_permission: str,
    session_factory: Any,    # Callable[[], Session]
    hybrid_gate: Any = None, # HybridGate | None — None skips AI evaluation
    actor: str = "system",
    actor_role: str = "DEVELOPER",
) -> Callable[[OrchestratorState], dict]:
    """Factory: returns a LangGraph gate node that interrupt()s for human approval.

    interrupt() serialises the pending payload to the checkpointer and raises
    a LangGraph GraphInterrupt exception.  LangGraph catches it, saves the
    graph state, and returns control to the caller.  The CLI resume command
    calls graph.invoke(Command(resume=human_input), config) to continue.
    """
    def node(state: OrchestratorState) -> dict:
        from orchestrator.governance.audit import AuditLogger

        run_id = state["run_id"]
        log = PipelineLogger(run_id=run_id, actor=actor, role=actor_role)

        session = session_factory()
        try:
            audit  = AuditLogger(session)
            _audit(audit, run_id, EventType.CHECKPOINT_REACHED,
                   details={"gate": gate_name, "required_permission": required_permission})
        finally:
            session.close()

        log.gate_reached(gate_name, required_permission, triggered_by=state.get("triggered_by", ""))

        # Pause — CLI receives this payload via GraphInterrupt
        approval = interrupt({
            "gate_name": gate_name,
            "run_id": run_id,
            "required_permission": required_permission,
            "stage_artifacts": state.get("stage_artifacts", {}),
        })

        # Resumed here after CLI calls graph.invoke(Command(resume=human_input), config)
        approved = approval.get("approved", False) if isinstance(approval, dict) else False
        approver = approval.get("approver", "unknown") if isinstance(approval, dict) else "unknown"
        comment  = approval.get("comment", "") if isinstance(approval, dict) else ""

        session2 = session_factory()
        try:
            audit2 = AuditLogger(session2)
            _audit(audit2, run_id,
                   EventType.CHECKPOINT_APPROVED if approved else EventType.CHECKPOINT_REJECTED,
                   details={"gate": gate_name, "approved": approved, "approver": approver})
        finally:
            session2.close()

        if approved:
            log.gate_approved(gate_name, approver, approver_role="APPROVER", comment=comment)
        else:
            log.gate_rejected(gate_name, approver, approver_role="APPROVER", comment=comment)

        return {"stage_artifacts": {
            **state.get("stage_artifacts", {}),
            gate_name: {"approved": approved, "approver": approver},
        }}

    node.__name__ = f"{gate_name}_node"
    return node


# ── private helpers ───────────────────────────────────────────────────────────


def _audit(
    audit: Any,
    run_id: str,
    event_type: Any,
    stage_name: str | None = None,
    details: dict | None = None,
) -> None:
    if audit is not None:
        audit.log(
            run_id=run_id,
            event_type=event_type,
            actor="system",
            stage_name=stage_name,
            details=details or {},
        )


def _persist_pr(session_factory: Any, run_id: str, pr_url: str, branch: str) -> None:
    """Write pr_url + feature_branch to orch_runs so the status command can show it."""
    session = session_factory()
    try:
        from orchestrator.state.store import RunStateStore
        RunStateStore(session).update_run_pr(run_id, pr_url, branch)
    except Exception:
        pass  # Non-fatal — the artifact already has the URL
    finally:
        session.close()
