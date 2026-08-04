"""
GreenFieldScenario — LangGraph pipeline for brand-new feature development.

Graph topology
--------------
This is the full 14-node pipeline for adding a net-new feature to the URL
shortener.  Every new endpoint or model goes through this flow:

  START
    └─ requirements_analysis    (understand + scope the requirement)
    └─ architecture_design      (design endpoints, models, migration plan)
    └─ architecture_gate        ← interrupt() — TECH_LEAD approval
    ├─ implementation_plan ─┐   (fan-out: two planning stages run concurrently)
    └─ test_plan           ─┤
                            └─ implementation     (write code via write_file + run_tests)
                               └─ [conditional]
                                  ├─ schema_gate       ← interrupt() if migration needed
                                  └─ unit_tests ──┐    (fan-out: both test types concurrent)
                                    integration_tests ─┤
                                                       └─ tests_gate  ← interrupt()
                                                          └─ documentation
                                                             └─ pr_gate  ← interrupt()
                                                                └─ release_readiness
                                                                   └─ release_gate  ← interrupt()
                                                                      └─ END

Human gates (interrupt() nodes)
--------------------------------
4 mandatory gates require human approval before proceeding:
  architecture_gate   — TECH_LEAD reviews architecture + AI evaluation
  tests_gate          — TECH_LEAD reviews test results
  pr_gate             — RELEASE_MANAGER approves PR creation
  release_gate        — RELEASE_MANAGER signs off on release checklist

1 conditional gate:
  schema_gate         — Only if implementation sets schema_change_detected=True
                        Requires RELEASE_MANAGER (DB migration has prod risk)

Conditional routing
-------------------
After implementation, add_conditional_edges reads state["schema_change_detected"]
to decide whether the migration review gate is needed.  This avoids an
unnecessary human interrupt for pure-logic changes with no DB migration.

Fan-out / fan-in
----------------
  architecture_gate → [implementation_plan, test_plan]  — concurrent planning
  [implementation_plan, test_plan] → implementation     — fan-in (wait for both)
  unit_tests + integration_tests   → tests_gate         — both must complete

LangGraph handles the concurrency automatically for any node with multiple
incoming edges (fan-in) or multiple outgoing edges (fan-out).
"""

from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from orchestrator.core.state import OrchestratorState
from orchestrator.scenarios.base import BaseScenario


class GreenFieldScenario(BaseScenario):
    """Full pipeline for net-new feature development."""

    def __init__(self, nodes: dict[str, Callable]) -> None:
        """
        Args:
            nodes: Mapping of node_name → node_function.
                   Produced by the node factories in scenarios/nodes.py.
                   Each function has signature (OrchestratorState) -> dict.
                   Accepting nodes as a dict keeps build_graph() testable with
                   simple lambda/mock functions.
        """
        self._nodes = nodes

    def build_graph(self, checkpointer: Any):
        """Build and compile the GreenField LangGraph StateGraph.

        Args:
            checkpointer: MemorySaver (tests) or PostgresSaver (production).

        Returns:
            Compiled LangGraph graph ready for .invoke(state, config).
        """
        graph = StateGraph(OrchestratorState)

        # ── Register nodes ────────────────────────────────────────────────────
        for name, fn in self._nodes.items():
            graph.add_node(name, fn)

        # ── Edges ─────────────────────────────────────────────────────────────
        graph.add_edge(START, "requirements_analysis")
        graph.add_edge("requirements_analysis", "architecture_design")
        graph.add_edge("architecture_design", "architecture_gate")

        # Fan-out: concurrent planning after architecture approval
        graph.add_edge("architecture_gate", "implementation_plan")
        graph.add_edge("architecture_gate", "test_plan")

        # Fan-in: implementation waits for both planning stages
        graph.add_edge(["implementation_plan", "test_plan"], "implementation")

        # Conditional: schema gate only when a DB migration is required.
        # No-schema path fans out to both test stages immediately.
        graph.add_conditional_edges(
            "implementation",
            lambda state: (
                "schema_gate"
                if state.get("schema_change_detected")
                else ["unit_tests", "integration_tests"]
            ),
        )
        # Schema path: fan-out to both test stages after schema review
        graph.add_edge("schema_gate", "unit_tests")
        graph.add_edge("schema_gate", "integration_tests")

        # Fan-in: both test types must complete before tests_gate
        graph.add_edge(["unit_tests", "integration_tests"], "tests_gate")

        graph.add_edge("tests_gate", "documentation")
        graph.add_edge("documentation", "pr_gate")
        graph.add_edge("pr_gate", "release_readiness")
        graph.add_edge("release_readiness", "release_gate")
        graph.add_edge("release_gate", END)

        return graph.compile(checkpointer=checkpointer)

    @classmethod
    def node_names(cls) -> list[str]:
        """All node names required by this scenario — used to validate the nodes dict."""
        return [
            "requirements_analysis",
            "architecture_design",
            "architecture_gate",
            "implementation_plan",
            "test_plan",
            "implementation",
            "schema_gate",
            "unit_tests",
            "integration_tests",
            "tests_gate",
            "documentation",
            "pr_gate",
            "release_readiness",
            "release_gate",
        ]
