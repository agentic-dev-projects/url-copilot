"""
EvaluationReport and HybridFeedback — the two output shapes of the hybrid evaluation flow.

EvaluationReport  — the AI model's (o1-mini) structured critique of a stage artifact.
                    Pure data: no logic, no DB interaction.

HybridFeedback    — the combined record of AI evaluation + human decision.
                    Stored in OrchestratorState["stage_evaluations"][stage_name] and
                    injected into all downstream stage prompts by Prompt Builder Layer 4
                    so the doer agent (gpt-4o) can learn from previous gate feedback.

Why two separate dataclasses?
------------------------------
EvaluationReport is what the AI produced — it exists regardless of the human decision.
HybridFeedback is what the gate decided — it wraps the AI report plus the human's comment
and approval.  Keeping them separate makes the audit trail clearer: EvaluationReport is
immutable once written; HybridFeedback adds the human layer on top.

Override semantics
------------------
override=True means the human approved despite one or more blocking_issues in the AI
report.  HybridGate logs this as CHECKPOINT_APPROVED_OVERRIDE in orch_audit_events with
the human's justification.  This satisfies SOC2 CC7.2 (human retains final authority but
every override is tamper-evidently logged).
"""

from dataclasses import dataclass
from typing import Literal


@dataclass
class EvaluationReport:
    """Structured output from o1-mini's critique of a single stage artifact.

    Scores 1-5:
        1 = fundamental design flaws — recommend REJECT
        2 = significant gaps likely to cause production issues
        3 = acceptable with concerns — APPROVE_WITH_NOTES
        4 = good design, minor suggestions only
        5 = excellent — APPROVE unconditionally
    """

    stage_name: str
    overall_score: int                                              # 1–5
    strengths: list[str]                                           # what was done well
    concerns: list[str]                                            # non-blocking observations
    blocking_issues: list[str]                                     # must be acknowledged to approve
    suggestions: list[str]                                         # optional improvements
    recommendation: Literal["APPROVE", "APPROVE_WITH_NOTES", "REJECT"]


@dataclass
class HybridFeedback:
    """Combined record of AI evaluation + human reviewer decision for one gate.

    Persisted to OrchestratorState["stage_evaluations"][stage_name] immediately
    after the gate completes so downstream stage prompts can reference it.
    """

    stage_name: str
    ai_evaluation: EvaluationReport
    human_comment: str                                             # may be empty string
    approved_by: str                                               # github_login of the approver
    override: bool = False                                         # True when approved despite blocking_issues
