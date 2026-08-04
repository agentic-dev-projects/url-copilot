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

from typing import Any, Callable

from langgraph.types import interrupt

from orchestrator.core.stage import StageResult, StageStatus
from orchestrator.core.state import OrchestratorState
from orchestrator.governance.audit import AuditLogger, EventType


def make_stage_node(
    stage_name: str,
    gateway: Any,            # AIGateway
    registry: Any,           # ToolRegistry
    session_factory: Any,    # Callable[[], Session] — called fresh per invocation
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

        session = session_factory()
        try:
            cache        = ResponseCache(session)
            memory_store = MemoryStore(session)
            run_store    = RunStateStore(session)
            audit        = AuditLogger(session)
            agent        = StageAgent(gateway, registry, cache)

            run_id = state["run_id"]
            _audit(audit, run_id, EventType.STAGE_STARTED, details={"stage": stage_name})

            result: StageResult = agent.run(stage_name, state, memory_store)
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
                _audit(audit, run_id, EventType.STAGE_FAILED,
                       stage_name=stage_name, details={"error": result.error_message})
                return {"stage_artifacts": {
                    **state.get("stage_artifacts", {}),
                    stage_name: {"error": result.error_message, "status": "failed"},
                }}

            _audit(audit, run_id, EventType.STAGE_COMPLETED, stage_name=stage_name)
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
) -> Callable[[OrchestratorState], dict]:
    """Factory: returns a LangGraph gate node that interrupt()s for human approval.

    interrupt() serialises the pending payload to the checkpointer and raises
    a LangGraph GraphInterrupt exception.  LangGraph catches it, saves the
    graph state, and returns control to the caller.  The CLI resume command
    calls graph.invoke(Command(resume=human_input), config) to continue.
    """
    def node(state: OrchestratorState) -> dict:
        from orchestrator.governance.audit import AuditLogger

        session = session_factory()
        try:
            audit  = AuditLogger(session)
            run_id = state["run_id"]
            _audit(audit, run_id, EventType.CHECKPOINT_REACHED,
                   details={"gate": gate_name, "required_permission": required_permission})
        finally:
            session.close()

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

        session2 = session_factory()
        try:
            audit2 = AuditLogger(session2)
            _audit(audit2, run_id,
                   EventType.CHECKPOINT_APPROVED if approved else EventType.CHECKPOINT_REJECTED,
                   details={"gate": gate_name, "approved": approved, "approver": approver})
        finally:
            session2.close()

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
