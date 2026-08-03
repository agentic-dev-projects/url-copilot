"""
orchestrator.planner — Requirement classifier and DAG selector.

Responsibilities
----------------
classifier.py   Single gpt-4o-mini call that classifies a requirement as:
                  - greenfield  (new feature, no existing code modified)
                  - brownfield  (modifies existing code)
                  - ambiguous   (unclear scope — triggers clarification loop)

planner.py      Selects the appropriate scenario DAG, creates the run record
                in orch_runs, and runs the clarification loop when the
                requirement is ambiguous.

Clarification loop (ambiguous only)
-------------------------------------
1. Read codebase + docs/design.md NFRs into context.
2. Generate 4 targeted questions via LLM.
3. Present questions to the developer at the CLI.
4. Collect answers.
5. LLM maps answers to a concrete scope.
6. Surface 3 assumptions for developer to confirm [y/n].
7. Save all decisions to orch_memory for downstream stages.
8. Set RunContext.resolved_requirement.

Implemented in Phase 14.
"""
