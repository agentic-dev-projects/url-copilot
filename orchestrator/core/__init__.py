"""
orchestrator.core — DAG engine, stage models, and shared run state.

Files
-----
stage.py    StageStatus enum, StageNode dataclass, StageResult dataclass.
            StageNode is the unit of work in the DAG; StageResult is what
            gets persisted to orch_stage_results after execution.

dag.py      DAGGraph — directed acyclic graph over StageNodes.
            Tracks dependencies, finds stages that are ready to run
            (all upstream deps COMPLETED), detects stuck states.

engine.py   OrchestrationEngine — the main execution loop.
            Runs parallel-ready stages concurrently, waits at sync points,
            triggers human gates, handles retries (max 3 attempts per stage).

context.py  RunContext dataclass — the shared state bag passed to every stage.
            Contains: run_id, requirement, stage_artifacts (outputs keyed by
            stage name), tool_cache (within-run read cache), feature_branch,
            pr_url, schema_change_detected flag, and stage_evaluations
            (HybridFeedback from the evaluator, injected into downstream prompts).

Implemented in Phase 3 (stage, dag, context) and Phase 13 (engine).
"""
