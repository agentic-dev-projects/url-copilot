"""
orchestrator.core.state — LangGraph state definition for the orchestrator.

OrchestratorState is the TypedDict that LangGraph uses as the shared state
passed between every node in the pipeline graph.  It replaces the RunContext
dataclass from the original design.

Why TypedDict instead of dataclass?
------------------------------------
LangGraph requires state to be a TypedDict (or Pydantic BaseModel).  At each
node, the function returns a partial dict — only the keys it modified — and
LangGraph merges that update into the full state using its built-in reducer.
This is more efficient than passing and copying a full dataclass on every node.

How LangGraph uses this
-----------------------
1. Planner creates the initial state dict and calls graph.invoke(state, config).
2. Each node function receives the full OrchestratorState and returns a dict
   with only the keys it changed.
3. LangGraph merges the update, checkpoints the new state to PostgreSQL via
   PostgresSaver, and advances to the next ready node(s).
4. At interrupt() nodes (human gates), LangGraph serializes state to DB and
   pauses.  Resuming is: graph.invoke(None, config) with the same thread_id.

State fields by phase
---------------------
Set by Planner (Phase 14):
    run_id, requirement, scenario_type, triggered_by

Set after clarification loop (ambiguous only):
    resolved_requirement, assumptions

Set incrementally as stages complete:
    stage_artifacts[stage_name] = output artifact dict

Set by HybridGate (Phase 3.5) after each evaluated stage:
    stage_evaluations[stage_name] = HybridFeedback (stored as dict for JSON serialisation)

Set by IMPLEMENTATION stage:
    feature_branch, schema_change_detected

Set by create_pr tool call:
    pr_url, pr_number
"""

from typing import Any, TypedDict


class OrchestratorState(TypedDict, total=False):
    """LangGraph state shared across all pipeline nodes.

    All fields are optional (total=False) so that each node can return a
    partial update dict containing only the keys it changed.  LangGraph
    merges the partial update into the full state automatically.

    Fields
    ------
    run_id                  Unique run identifier, e.g. "orch-green-001".
                            Primary key in orch_runs and FK in all orch_ tables.

    requirement             Raw natural-language requirement from the user.

    resolved_requirement    Set after the clarification loop (ambiguous only).
                            Contains the fully scoped requirement with all
                            ambiguities resolved.

    scenario_type           "greenfield" | "brownfield" | "ambiguous".
                            Set by the Planner after classification.

    triggered_by            github_login of the user who started this run.
                            Used for four-eyes: approver.github_login != triggered_by.

    stage_artifacts         Dict of stage_name → output artifact.
                            Populated after each stage completes.
                            Injected into Prompt Builder Layer 4 so downstream
                            stages can read prior decisions.

    stage_evaluations       Dict of stage_name → HybridFeedback (as dict, for
                            JSON serialisation through LangGraph checkpointer).
                            Injected into Prompt Builder Layer 4 so downstream
                            doer agents see prior AI scores and human comments.

    feature_branch          Git branch created for this run's code changes.
                            Example: "orch/feature/qr-code-orch-green-001"

    pr_url                  GitHub PR URL, set after create_pr tool call.

    pr_number               GitHub PR number (int), used by poll_pr_status.

    schema_change_detected  True if IMPLEMENTATION stage detected a DB migration.
                            Triggers Gate #2 (approve_schema_change) via a
                            conditional edge in the LangGraph pipeline.

    assumptions             Confirmed assumption strings from the clarification
                            loop (ambiguous scenario only).  Saved to orch_memory
                            and included in the PR body.

    tool_cache              Within-run cache for tool results, keyed on
                            SHA-256(tool_name + json(args)).
                            NOT checkpointed to DB — in-memory only.
                            Evicted when the run ends.
    """
    run_id:                 str
    requirement:            str
    resolved_requirement:   str
    scenario_type:          str
    triggered_by:           str
    stage_artifacts:        dict[str, Any]
    stage_evaluations:      dict[str, Any]
    feature_branch:         str | None
    pr_url:                 str | None
    pr_number:              int | None
    schema_change_detected: bool
    assumptions:            list[str]
    tool_cache:             dict[str, Any]
