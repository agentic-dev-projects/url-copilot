"""
orchestrator.scenarios — DAG definitions for the three scenario types.

Overview
--------
A scenario translates a classified requirement type into a concrete DAG of
StageNodes with dependencies, gate requirements, and retry limits.  The
OrchestrationEngine executes the DAG without knowing which scenario type
produced it — the DAG is the full specification.

Scenario types
--------------
GreenFieldScenario   New feature — no existing code modified (only new files
                     + router registration).  Agent does not need to read
                     existing code before proposing architecture.

BrownfieldScenario   Modifies existing code.  REQUIREMENTS_ANALYSIS and
                     ARCHITECTURE_DESIGN prompts instruct the agent to read
                     current code before proposing any changes.  Impact
                     analysis is a required section of the architecture artifact.

AmbiguousScenario    Unclear scope.  The Planner runs a clarification loop
                     (up to 2 rounds of questions) before the DAG executes.
                     RunContext.resolved_requirement is set from the loop
                     output and used as the basis for all subsequent stages.

Shared DAG structure
--------------------
All three scenarios share the same 9-stage structure and dependency graph:

  REQUIREMENTS_ANALYSIS → ARCHITECTURE_DESIGN → [IMPL_PLAN ‖ TEST_PLAN]
  → (sync) → IMPLEMENTATION → [UNIT_TESTS ‖ INTEGRATION_TESTS]
  → (sync) → DOCUMENTATION → RELEASE_READINESS

The differences are in the per-stage prompts and the Planner's pre-flight
work, not in the DAG topology itself.

Files
-----
base.py         BaseScenario abstract class with build_dag() interface.
greenfield.py   GreenFieldScenario(BaseScenario)
brownfield.py   BrownfieldScenario(BaseScenario)
ambiguous.py    AmbiguousScenario(BaseScenario)

Implemented in Phase 15.
"""
