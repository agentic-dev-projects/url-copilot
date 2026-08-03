"""
orchestrator.evaluator — Hybrid LLM-as-Judge evaluation component.

Overview
--------
The evaluator runs at 4 key SDLC stages — architecture design, implementation,
tests (unit + integration combined), and release readiness — before the human
approval gate.  A second AI model (o1-mini, a reasoning model) independently
reviews the doer agent's output and produces a structured critique.  The human
reviewer then sees both the AI evaluation and the stage artifact, adds their
comment, and the combined HybridFeedback is stored in RunContext.stage_evaluations
and injected into all downstream stage prompts via Prompt Builder Layer 4.

Why a second model?
-------------------
Using a different model (o1-mini) from the doer (gpt-4o) prevents echo-chamber
validation.  o1-mini is a reasoning model — slower and more expensive per call
than gpt-4o-mini, but better at finding logical gaps, missing error handling,
and incorrect assumptions.

Why not just a human review?
------------------------------
Human reviewers are expensive and context-switches are costly.  The AI pre-review
surfaces obvious issues cheaply and quickly, so the human can focus on judgment
calls rather than mechanical checks.  The combination (AI catches the obvious,
human catches the subtle) outperforms either alone.

Files
-----
evaluation_report.py    EvaluationReport dataclass (score 1-5, strengths,
                        concerns, blocking_issues, recommendation) and
                        HybridFeedback dataclass (AI eval + human comment).

validator_agent.py      ValidatorAgent — calls o1-mini with a stage-specific
                        critic prompt and parses the structured JSON response
                        into an EvaluationReport.

hybrid_gate.py          HybridGate — displays the AI evaluation at the CLI,
                        collects the human reviewer's comment and [y/n] decision,
                        logs the result to orch_audit_events, saves the comment
                        to orch_memory, and writes HybridFeedback to RunContext.

Compliance note
---------------
If a human approves despite blocking_issues in the AI evaluation, the event is
logged as CHECKPOINT_APPROVED_OVERRIDE with the human's justification stored in
orch_audit_events.details.  This creates a tamper-evident audit trail that
satisfies SOC2 change-control requirements.

Implemented in Phase 3.5.
"""
