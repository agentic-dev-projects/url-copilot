"""
orchestrator.core.context — RunContext: the shared state bag for a single run.

RunContext is created once by the Planner at the start of a run and passed to
every stage, every tool call, and every gate check throughout that run's lifetime.
It is the single source of truth for "what has happened so far in this run".

Think of it like a database transaction context or a web request context:
every component that participates in the operation gets the same object, and
any state they produce is written back into it so subsequent components can
read it.

Key fields by phase
-------------------
Set by Planner (Phase 14):
    run_id, requirement, scenario_type, triggered_by

Set after clarification loop (ambiguous requirements only):
    resolved_requirement, assumptions

Set after ARCHITECTURE_DESIGN gate:
    stage_evaluations["architecture_design"]  ← HybridFeedback

Set after IMPLEMENTATION (Phase 13 engine):
    feature_branch, schema_change_detected

Set after PR creation tool call:
    pr_url, pr_number

Set by HybridGate (Phase 3.5) after each evaluated stage:
    stage_evaluations[stage_name]  ← HybridFeedback injected into Layer 4
                                      of all downstream stage prompts

Within-run only (not persisted):
    tool_cache  ← avoids redundant file reads within a run

Note on stage_evaluations typing
---------------------------------
The value type is `Any` here to avoid a circular import: HybridFeedback is
defined in orchestrator.evaluator.evaluation_report (Phase 3.5), which is
built after this module.  Phase 3.5 can safely annotate the dict values as
HybridFeedback in its own code without touching this file.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunContext:
    """Shared state bag passed to every component throughout a single run.

    Attributes
    ----------
    run_id                  Unique run identifier, e.g. "orch-green-001".
                            Used as the primary key in orch_runs and as the
                            FK in all other orch_ tables.

    requirement             Raw natural-language requirement as typed by the user.

    resolved_requirement    Set after the clarification loop (ambiguous scenarios).
                            Contains the fully scoped requirement with all
                            ambiguities resolved.  Equals requirement for
                            greenfield/brownfield scenarios.

    scenario_type           "greenfield" | "brownfield" | "ambiguous"
                            Set by the Planner after classification.

    triggered_by            github_login of the user who started this run.
                            Used for four-eyes enforcement: the approver of any
                            gate must have a different github_login than this.

    stage_artifacts         Dict of stage_name → output artifact (dict).
                            Populated after each stage completes.
                            Injected into Prompt Builder Layer 4 so downstream
                            stages can read prior decisions.

    tool_cache              Within-run cache for tool results, keyed on
                            SHA-256(tool_name + json(args)).
                            Avoids re-reading the same file multiple times in
                            one run.  Evicted when the run ends.

    feature_branch          Git branch name created for this run's code changes.
                            Set by the create_branch tool call in IMPLEMENTATION.
                            Example: "orch/feature/qr-code-orch-green-001"

    pr_url                  GitHub PR URL, set after create_pr tool call.

    pr_number               GitHub PR number (integer), used by poll_pr_status.

    schema_change_detected  Set to True by the IMPLEMENTATION stage agent if it
                            detects a DB schema change in the output artifact.
                            Triggers Gate #2 (approve_schema_change) in the engine.

    assumptions             List of assumption strings confirmed during the
                            clarification loop (ambiguous scenario only).
                            Saved to orch_memory and included in the PR body.

    stage_evaluations       Dict of stage_name → HybridFeedback (typed as Any
                            to avoid circular import with orchestrator.evaluator).
                            Populated by HybridGate after each evaluated stage.
                            Injected into Prompt Builder Layer 4 so downstream
                            stages see prior AI evaluation scores and human
                            reviewer comments.
    """
    run_id:                 str
    requirement:            str
    resolved_requirement:   str             = ""
    scenario_type:          str             = ""
    triggered_by:           str             = ""
    stage_artifacts:        dict[str, Any]  = field(default_factory=dict)
    tool_cache:             dict[str, Any]  = field(default_factory=dict)
    feature_branch:         str | None      = None
    pr_url:                 str | None      = None
    pr_number:              int | None      = None
    schema_change_detected: bool            = False
    assumptions:            list[str]       = field(default_factory=list)
    stage_evaluations:      dict[str, Any]  = field(default_factory=dict)
