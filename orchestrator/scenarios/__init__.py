"""
orchestrator.scenarios — LangGraph pipeline definitions for the three scenario types.

Overview
--------
A scenario builds a compiled LangGraph StateGraph for a given requirement type.
The OrchestrationEngine (Phase 13) calls scenario.build_graph() to get the
compiled graph, then invokes it with the initial OrchestratorState.

Scenario types
--------------
GreenFieldScenario   New feature — no existing code modified.
BrownfieldScenario   Modifies existing code.  REQUIREMENTS_ANALYSIS and
                     ARCHITECTURE_DESIGN nodes instruct the agent to read
                     current code before proposing changes.
AmbiguousScenario    Unclear scope.  Planner runs clarification loop first;
                     resolved_requirement is set in OrchestratorState before
                     the graph executes.

LangGraph graph structure (all three scenarios share this topology)
--------------------------------------------------------------------

    START
      │
    requirements_analysis
      │
    architecture_design
      │
    architecture_gate          ← interrupt() — hybrid eval + human [y/n]
      │
    ┌─┴─────────────────┐
    implementation_plan  test_plan    (parallel fan-out)
    └─────────┬──────────┘
              │  (fan-in sync — LangGraph waits for both)
          implementation
              │
    ┌─────────┴──────────┐  conditional edge: schema_change_detected?
    │                    │
  schema_gate         (skip)
    │                    │
    └────────────────────┘
              │
    ┌─────────┴──────────┐
    unit_tests  integration_tests     (parallel fan-out)
    └─────────┬──────────┘
              │  (fan-in sync)
          tests_gate             ← interrupt() — hybrid eval + human [y/n]
              │
          documentation
              │
          pr_gate                ← interrupt() — wait for GitHub PR merge
              │
          release_readiness
              │
          release_gate           ← interrupt() — hybrid eval + human [y/n]
              │
            END

Key LangGraph concepts used
----------------------------
add_node(name, fn)              Each node is a plain Python function:
                                (OrchestratorState) → dict (partial state update)

add_edge(a, b)                  b runs after a completes.

add_edge([a, b], c)             Fan-in: c waits for both a and b (sync point).

add_conditional_edges(a, fn)    Branch: fn(state) returns the next node name.
                                Used for the schema change gate (Gate #2).

interrupt(payload)              Human-in-the-loop pause.  LangGraph serialises
                                state to PostgreSQL and stops.  Resume by calling
                                graph.invoke(None, config) with the same thread_id.

compile(checkpointer=saver)     Bakes the graph into an executable with
                                PostgresSaver for state persistence.

Files
-----
base.py         BaseScenario with build_graph() interface.
greenfield.py   GreenFieldScenario(BaseScenario)
brownfield.py   BrownfieldScenario(BaseScenario)
ambiguous.py    AmbiguousScenario(BaseScenario)

Implemented in Phase 15.
"""
