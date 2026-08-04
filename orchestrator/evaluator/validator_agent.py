"""
ValidatorAgent — calls o1-mini with a stage-specific critic prompt and parses the
structured JSON response into an EvaluationReport.

Design
------
ValidatorAgent is intentionally thin — one LLM call, one JSON parse, one dataclass.
No side effects other than the two audit events it logs (EVALUATOR_STARTED,
EVALUATOR_COMPLETED).  All orchestration (display, human prompts, audit flow) lives
in HybridGate.

Why a _GatewayCallable Protocol instead of importing AIGateway directly?
------------------------------------------------------------------------
AIGateway is built in Phase 7 — after this phase.  Defining a Protocol with only
the subset of the interface ValidatorAgent actually needs (a single .call() method)
means:
  1. This file compiles and tests without the gateway being built.
  2. Any object that satisfies the Protocol works — easy to mock in tests.
  3. The real AIGateway (Phase 7) will satisfy the Protocol without modification.
This is the Dependency Inversion Principle applied at the type level.

JSON parsing robustness
-----------------------
o1-mini usually returns clean JSON.  Occasionally it wraps the output in a markdown
code fence (```json ... ```).  _parse_response() strips those before parsing.
Malformed JSON raises ValueError — the caller (HybridGate or engine retry logic)
decides whether to retry.

Prompt loading
--------------
Prompt files live at orchestrator/prompts/evaluator/eval_{stage}_v1.txt.
PROMPTS_DIR is resolved relative to this file's location so it works regardless of
the current working directory.
"""

import json
import re
from pathlib import Path
from typing import Any, Protocol

from orchestrator.core.state import OrchestratorState
from orchestrator.evaluator.evaluation_report import EvaluationReport
from orchestrator.gateway.models import GatewayRequest, GatewayResponse

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

_VALID_RECOMMENDATIONS = {"APPROVE", "APPROVE_WITH_NOTES", "REJECT"}


class _GatewayCallable(Protocol):
    """Minimal interface ValidatorAgent needs from AIGateway (Phase 7).

    Now that AIGateway is built, this Protocol mirrors its actual call() signature
    so that any object satisfying it (real AIGateway, or a test mock) works here.
    """

    def call(self, request: GatewayRequest) -> GatewayResponse: ...


class ValidatorAgent:
    """Runs the AI half of the hybrid evaluation: calls o1-mini, returns EvaluationReport."""

    def __init__(self, gateway: _GatewayCallable, config: dict) -> None:
        """
        Args:
            gateway:  Any object satisfying _GatewayCallable (real AIGateway in Phase 7).
            config:   Parsed evaluator.yaml dict.  Must contain:
                        validator_model: str        — "o1-mini"
                        prompts: dict[str, str]     — stage_name → relative prompt path
        """
        self.gateway = gateway
        self.validator_model: str = config["validator_model"]
        self.prompt_map: dict[str, str] = config["prompts"]

    def evaluate(
        self,
        stage_name: str,
        stage_artifact: dict,
        state: OrchestratorState,
        audit: Any,  # AuditLogger — implemented in Phase 5; None is safe
    ) -> EvaluationReport:
        """Evaluate a stage artifact and return a structured EvaluationReport.

        Logs EVALUATOR_STARTED before the LLM call and EVALUATOR_COMPLETED after.
        Both audit events are no-ops if audit is None (safe during early phases).

        Args:
            stage_name:      One of the keys in evaluator.yaml enabled_stages.
            stage_artifact:  The output dict produced by the stage agent node.
            state:           Current OrchestratorState (for run_id, triggered_by).
            audit:           AuditLogger instance, or None (Phase 5).

        Returns:
            EvaluationReport with score 1-5, lists of findings, and recommendation.

        Raises:
            ValueError: if the LLM returns malformed JSON or an unknown recommendation.
            KeyError:   if stage_name is not in self.prompt_map (misconfigured yaml).
        """
        prompt_path = PROMPTS_DIR / self.prompt_map[stage_name]
        critic_prompt = prompt_path.read_text(encoding="utf-8")

        messages: list[dict] = [
            {"role": "system", "content": critic_prompt},
            {"role": "user", "content": json.dumps(stage_artifact, indent=2)},
        ]

        _audit_log(
            audit,
            run_id=state["run_id"],
            event_type="EVALUATOR_STARTED",
            actor="system",
            details={"stage_name": stage_name, "model": self.validator_model},
        )

        response: GatewayResponse = self.gateway.call(
            GatewayRequest(
                token=state.get("triggered_by", ""),
                run_id=state["run_id"],
                stage_name=f"eval_{stage_name}",
                messages=messages,
                model=self.validator_model,
                prompt_version=f"eval_{stage_name}_v1",
            )
        )
        raw_response: str = response.content or ""

        report = self._parse_response(stage_name, raw_response)

        _audit_log(
            audit,
            run_id=state["run_id"],
            event_type="EVALUATOR_COMPLETED",
            actor="system",
            details={
                "stage_name": stage_name,
                "score": report.overall_score,
                "recommendation": report.recommendation,
                "blocking_issues_count": len(report.blocking_issues),
            },
        )

        return report

    # ── private ──────────────────────────────────────────────────────────────

    def _parse_response(self, stage_name: str, raw: str) -> EvaluationReport:
        """Strip markdown fences if present, parse JSON, validate keys, return dataclass."""
        json_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)

        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"ValidatorAgent: malformed JSON from {self.validator_model} "
                f"for stage '{stage_name}': {exc}\n--- raw ---\n{raw}"
            ) from exc

        required_keys = {
            "overall_score", "strengths", "concerns",
            "blocking_issues", "suggestions", "recommendation",
        }
        missing = required_keys - data.keys()
        if missing:
            raise ValueError(
                f"ValidatorAgent: missing keys in evaluation response for '{stage_name}': {missing}"
            )

        recommendation = data["recommendation"]
        if recommendation not in _VALID_RECOMMENDATIONS:
            raise ValueError(
                f"ValidatorAgent: unknown recommendation '{recommendation}' "
                f"(expected one of {_VALID_RECOMMENDATIONS})"
            )

        return EvaluationReport(
            stage_name=stage_name,
            overall_score=int(data["overall_score"]),
            strengths=list(data["strengths"]),
            concerns=list(data["concerns"]),
            blocking_issues=list(data["blocking_issues"]),
            suggestions=list(data["suggestions"]),
            recommendation=recommendation,
        )


# ── module-level helper ───────────────────────────────────────────────────────

def _audit_log(audit: Any, *, run_id: str, event_type: str, actor: str, details: dict) -> None:
    """Call audit.log() if audit is not None.  Keeps call sites clean."""
    if audit is not None:
        audit.log(run_id=run_id, event_type=event_type, actor=actor, details=details)
