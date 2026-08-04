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
    stage_agent: Any,        # StageAgent — Any avoids circular import
    memory_store: Any,       # MemoryStore
    run_store: Any,          # RunStateStore
    audit: Any,              # AuditLogger | None
) -> Callable[[OrchestratorState], dict]:
    """Factory: returns a LangGraph node function for stage_name.

    The returned function:
      1. Logs STAGE_STARTED to audit
      2. Runs StageAgent.run() — multi-turn LLM + tool loop
      3. Persists StageResult to orch_stage_results via RunStateStore
      4. Logs STAGE_COMPLETED or STAGE_FAILED to audit
      5. Returns dict with updated stage_artifacts

    Args:
        stage_name:   e.g. "architecture_design"
        stage_agent:  Configured StageAgent instance
        memory_store: MemoryStore for Layer 3 prompts
        run_store:    RunStateStore for persisting StageResult
        audit:        AuditLogger (or None — safe to omit in tests)
    """
    def node(state: OrchestratorState) -> dict:
        run_id = state["run_id"]

        _audit(audit, run_id, EventType.STAGE_STARTED, details={"stage": stage_name})

        result: StageResult = stage_agent.run(stage_name, state, memory_store)
        run_store.save_stage_result(result, run_id)

        if result.status == StageStatus.FAILED:
            _audit(audit, run_id, EventType.STAGE_FAILED,
                   stage_name=stage_name, details={"error": result.error_message})
            # Surface the error as a state flag so the engine can handle it
            return {"stage_artifacts": {
                **state.get("stage_artifacts", {}),
                stage_name: {"error": result.error_message, "status": "failed"},
            }}

        _audit(audit, run_id, EventType.STAGE_COMPLETED, stage_name=stage_name)
        return {"stage_artifacts": {
            **state.get("stage_artifacts", {}),
            stage_name: result.output_artifact,
        }}

    node.__name__ = f"{stage_name}_node"
    return node


def make_gate_node(
    gate_name: str,
    required_permission: str,
    audit: Any,              # AuditLogger | None
    hybrid_gate: Any,        # HybridGate | None — None skips AI evaluation
) -> Callable[[OrchestratorState], dict]:
    """Factory: returns a LangGraph gate node that interrupt()s for human approval.

    interrupt() serialises the pending payload to the checkpointer and raises
    a LangGraph GraphInterrupt exception.  LangGraph catches it, saves the
    graph state, and returns control to the caller.  The CLI resume command
    calls graph.invoke(approval_input, config) to continue from this point.

    Args:
        gate_name:           e.g. "architecture_gate"
        required_permission: Permission the approver must hold, e.g. "approve_architecture"
        audit:               AuditLogger or None
        hybrid_gate:         HybridGate for AI evaluation before human prompt (or None)
    """
    def node(state: OrchestratorState) -> dict:
        run_id = state["run_id"]
        _audit(audit, run_id, EventType.CHECKPOINT_REACHED,
               details={"gate": gate_name, "required_permission": required_permission})

        # Pause execution — CLI receives this payload via the GraphInterrupt
        approval = interrupt({
            "gate_name": gate_name,
            "run_id": run_id,
            "required_permission": required_permission,
            "stage_artifacts": state.get("stage_artifacts", {}),
        })

        # When graph.invoke(approval_input, config) is called, execution resumes here.
        # approval contains whatever the CLI passed as human_input.
        approved = approval.get("approved", False) if isinstance(approval, dict) else False
        approver = approval.get("approver", "unknown") if isinstance(approval, dict) else "unknown"

        _audit(audit, run_id,
               EventType.CHECKPOINT_APPROVED if approved else EventType.CHECKPOINT_REJECTED,
               details={"gate": gate_name, "approved": approved, "approver": approver})

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
