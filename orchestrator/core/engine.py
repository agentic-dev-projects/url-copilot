"""
OrchestrationEngine — runs a compiled LangGraph pipeline for one orchestrator run.

The engine is intentionally thin — it delegates topology entirely to the
scenario and is responsible only for:
  1. Building the graph (calling scenario.build_graph(checkpointer))
  2. Invoking the graph for new runs (run)
  3. Resuming interrupted graphs after human gate approval (resume)

LangGraph handles everything else:
  - State checkpointing after every node (PostgresSaver)
  - Crash recovery (same thread_id resumes from the last checkpoint)
  - Parallel fan-out / fan-in
  - interrupt() gate suspension

Checkpointer injection
----------------------
The engine accepts a checkpointer at construction time rather than creating
one internally.  This keeps the engine testable: pass MemorySaver in tests,
pass PostgresSaver in production.  The PostgresSaver API requires a psycopg
connection (not a SQLAlchemy session) — the caller handles that translation.

thread_id
---------
LangGraph uses thread_id to associate checkpoints with a specific run.  We
use OrchestratorState["run_id"] as the thread_id so checkpoints are
deterministically tied to the DB record in orch_runs.

resume() vs run()
-----------------
run()    — first invocation; passes initial_state as the graph input.
resume() — continues a previously interrupted graph; passes human_input
           (the approval payload from the CLI) as the graph input.  LangGraph
           restores the last checkpoint and continues from the interrupt() call.
"""

from typing import Any

from langgraph.types import Command

from orchestrator.core.state import OrchestratorState
from orchestrator.scenarios.base import BaseScenario


class OrchestrationEngine:
    """Thin wrapper that builds and invokes LangGraph pipelines."""

    def __init__(self, scenario: BaseScenario, checkpointer: Any) -> None:
        """
        Args:
            scenario:     A BaseScenario subclass that defines the graph topology.
            checkpointer: MemorySaver (tests) or PostgresSaver (production).
        """
        self.scenario = scenario
        self.checkpointer = checkpointer
        self._graph = None   # lazy-compiled on first use

    def _get_graph(self):
        """Build and cache the compiled graph (once per engine instance)."""
        if self._graph is None:
            self._graph = self.scenario.build_graph(self.checkpointer)
        return self._graph

    def run(self, initial_state: OrchestratorState) -> OrchestratorState:
        """Start a new run from initial_state.

        Args:
            initial_state: OrchestratorState with at least run_id, requirement,
                           scenario_type, and triggered_by populated.

        Returns:
            Final OrchestratorState after the graph completes (or after
            interrupt() is hit — in that case the caller must call resume()).
        """
        graph = self._get_graph()
        config = {"configurable": {"thread_id": initial_state["run_id"]}}
        return graph.invoke(initial_state, config)

    def resume(self, run_id: str, human_input: dict) -> OrchestratorState:
        """Resume an interrupted graph after human gate approval.

        Called by the CLI `approve` command after a gate node has suspended
        execution via interrupt().  LangGraph loads the last checkpoint for
        this thread_id, re-enters the graph at the interrupt() call site,
        and continues execution from there.

        Args:
            run_id:      The orch_runs.id — used as LangGraph thread_id.
            human_input: The approval payload the CLI collected from the user.
                         Shape: {"approved": bool, "approver": str, "comment": str}

        Returns:
            Updated OrchestratorState after resuming (may interrupt again at
            the next gate).
        """
        graph = self._get_graph()
        config = {"configurable": {"thread_id": run_id}}
        return graph.invoke(Command(resume=human_input), config)

    def get_state(self, run_id: str) -> Any:
        """Return the current LangGraph checkpoint state for a run.

        Useful for the CLI `status` command to inspect a paused run without
        resuming it.

        Args:
            run_id: The orch_runs.id / LangGraph thread_id.

        Returns:
            LangGraph StateSnapshot for this thread.
        """
        graph = self._get_graph()
        config = {"configurable": {"thread_id": run_id}}
        return graph.get_state(config)
