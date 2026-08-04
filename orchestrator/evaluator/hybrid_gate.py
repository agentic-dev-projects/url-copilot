"""
HybridGate — orchestrates the full human-in-the-loop evaluation flow for a single gate.

Flow
----
1. ValidatorAgent.evaluate()     → EvaluationReport (AI critique via o1-mini)
2. _display_evaluation()         → Print score, strengths, concerns, blocking issues to CLI
3. _verify_approver()            → RBACCheckpoint: role check + four-eyes (trigger ≠ approver)
4. _prompt_human()               → Collect comment + [y/n] from CLI
5. Audit event                   → CHECKPOINT_APPROVED | CHECKPOINT_REJECTED | CHECKPOINT_APPROVED_OVERRIDE
6. Memory save                   → Human comment saved to orch_memory for future runs
7. Return HybridFeedback         → Stored in OrchestratorState["stage_evaluations"][stage_name]

Why HybridGate owns all side effects
--------------------------------------
ValidatorAgent is deliberately pure (one LLM call, one parse, return dataclass).
All audit writes, memory writes, and CLI interaction belong to HybridGate.  This
separation makes ValidatorAgent trivially testable in isolation: mock the gateway,
call evaluate(), assert the returned EvaluationReport.  Testing HybridGate requires
mocking more dependencies but is still straightforward.

Override semantics
------------------
If the AI report has blocking_issues AND the human approves, override=True is set
on HybridFeedback.  This triggers a CHECKPOINT_APPROVED_OVERRIDE audit event with:
  - blocking_issues list
  - human justification text
  - approver identity
These three facts together satisfy SOC2 CC7.2 (change control with documented exceptions).

LangGraph integration note (Phase 15)
--------------------------------------
In the compiled LangGraph graph, each gate is a node function that calls
langgraph.types.interrupt(payload) to pause the run and persist state to PostgreSQL.
When the human resumes the run (graph.invoke(human_response, config)), the gate node
calls HybridGate.run() with the approver_token from human_response.

HybridGate.run() handles the domain logic; interrupt() handles the persistence
mechanism.  They are separate layers — HybridGate does not import langgraph.

Unbuilt dependencies
--------------------
checkpoint   RBACCheckpoint — Phase 6.  Passing None skips four-eyes check.
audit        AuditLogger    — Phase 5.  Passing None skips audit writes.
memory       MemoryStore    — Phase 8.  Passing None skips memory persistence.
All three are annotated Any with phase comments so the injection points are obvious.
"""

from dataclasses import asdict
from typing import Any

from orchestrator.core.state import OrchestratorState
from orchestrator.evaluator.evaluation_report import EvaluationReport, HybridFeedback
from orchestrator.evaluator.validator_agent import ValidatorAgent, _audit_log


class ApprovalRejectedError(Exception):
    """Raised when a human reviewer rejects a gate.  Engine treats this as a run failure."""


class HybridGate:
    """Runs AI evaluation then collects a human approval decision at a pipeline gate."""

    def __init__(
        self,
        validator: ValidatorAgent,
        checkpoint: Any,   # RBACCheckpoint — Phase 6; None is safe
        audit: Any,        # AuditLogger    — Phase 5; None is safe
        memory: Any,       # MemoryStore    — Phase 8; None is safe
    ) -> None:
        self.validator = validator
        self.checkpoint = checkpoint
        self.audit = audit
        self.memory = memory

    def run(
        self,
        stage_name: str,
        stage_artifact: dict,
        state: OrchestratorState,
        required_permission: str,
        approver_token: str,
    ) -> HybridFeedback:
        """Run the full hybrid evaluation gate and return a HybridFeedback record.

        Args:
            stage_name:          Name of the stage being evaluated (e.g. "architecture_design").
            stage_artifact:      Output dict produced by the stage agent.
            state:               Current OrchestratorState (run_id, triggered_by, etc.).
            required_permission: RBAC permission the approver must hold (e.g. "approve_architecture").
            approver_token:      CLI token identifying the human reviewer.

        Returns:
            HybridFeedback combining the AI report and human decision.

        Raises:
            ApprovalRejectedError: if the human reviewer says no.
            ValueError:            if ValidatorAgent cannot parse the LLM response.
        """
        # 1. AI evaluation
        report: EvaluationReport = self.validator.evaluate(
            stage_name, stage_artifact, state, self.audit
        )

        # 2. Display to CLI
        self._display_evaluation(stage_name, report)

        # 3. Verify approver role + four-eyes
        approver = self._verify_approver(state, required_permission, approver_token)

        # 4. Collect human decision
        comment, approved = self._prompt_human(stage_name, report)

        # 5. Audit event
        has_blocking = bool(report.blocking_issues)
        if not approved:
            event_type = "CHECKPOINT_REJECTED"
            details: dict = {"stage": stage_name, "approver": approver, "comment": comment}
        elif has_blocking:
            event_type = "CHECKPOINT_APPROVED_OVERRIDE"
            details = {
                "stage": stage_name,
                "approver": approver,
                "blocking_issues": report.blocking_issues,
                "justification": comment,
            }
        else:
            event_type = "CHECKPOINT_APPROVED"
            details = {"stage": stage_name, "approver": approver, "comment": comment}

        _audit_log(
            self.audit,
            run_id=state["run_id"],
            event_type=event_type,
            actor=approver,
            details=details,
        )

        if not approved:
            raise ApprovalRejectedError(
                f"Stage '{stage_name}' rejected by {approver}. Comment: {comment!r}"
            )

        # 6. Save non-empty human comments to memory for future runs
        if comment and self.memory is not None:
            self.memory.save(
                run_id=state["run_id"],
                memory_type="preference",
                actor=approver,
                content=f"[{stage_name}] {comment}",
            )

        # 7. Build and return feedback
        return HybridFeedback(
            stage_name=stage_name,
            ai_evaluation=report,
            human_comment=comment,
            approved_by=approver,
            override=has_blocking,
        )

    # ── private helpers ───────────────────────────────────────────────────────

    def _display_evaluation(self, stage_name: str, report: EvaluationReport) -> None:
        """Print the AI evaluation to stdout in a human-readable format."""
        width = 72
        title = stage_name.upper().replace("_", " ")
        print(f"\n{'=' * width}")
        print(f"  AI EVALUATION — {title}")
        print(f"{'=' * width}")

        score_bar = "★" * report.overall_score + "☆" * (5 - report.overall_score)
        print(f"  Score : {report.overall_score}/5  {score_bar}   [{report.recommendation}]")
        print()

        if report.strengths:
            print("  Strengths:")
            for item in report.strengths:
                print(f"    ✓ {item}")
            print()

        if report.concerns:
            print("  Concerns (non-blocking):")
            for item in report.concerns:
                print(f"    ~ {item}")
            print()

        if report.blocking_issues:
            print(f"  ⚠  BLOCKING ISSUES — must acknowledge to approve:")
            for item in report.blocking_issues:
                print(f"    ✗ {item}")
            print()

        if report.suggestions:
            print("  Suggestions:")
            for item in report.suggestions:
                print(f"    → {item}")

        print(f"{'=' * width}\n")

    def _verify_approver(
        self,
        state: OrchestratorState,
        required_permission: str,
        approver_token: str,
    ) -> str:
        """Return the approver's login after verifying role + four-eyes constraint.

        Falls back to a token-derived login when RBACCheckpoint is not yet available
        (Phase 6 not built).  The fallback is clearly not production-safe — it exists
        so Phase 3.5 tests can run without Phase 6.
        """
        if self.checkpoint is not None:
            return self.checkpoint.request_approval(
                run_id=state["run_id"],
                required_permission=required_permission,
                trigger_user=state.get("triggered_by", ""),
                approver_token=approver_token,
            )
        # Fallback: derive a display name from the token (e.g. "bob_tl_token" → "bob")
        return approver_token.split("_")[0] if "_" in approver_token else approver_token

    def _prompt_human(
        self, stage_name: str, report: EvaluationReport
    ) -> tuple[str, bool]:
        """Prompt the reviewer for a comment and a yes/no approval decision."""
        if report.blocking_issues:
            print(
                f"  The AI flagged {len(report.blocking_issues)} blocking issue(s).\n"
                "  Approving will log a CHECKPOINT_APPROVED_OVERRIDE audit event.\n"
            )

        comment = input(f"  Comment on '{stage_name}' (Enter to skip): ").strip()
        while True:
            answer = input(f"  Approve '{stage_name}'? [y/n]: ").strip().lower()
            if answer in ("y", "yes"):
                return comment, True
            if answer in ("n", "no"):
                return comment, False
            print("  Please enter 'y' or 'n'.")
