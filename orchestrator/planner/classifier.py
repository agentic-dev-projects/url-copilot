"""
RequirementClassifier — classifies a requirement into greenfield/brownfield/ambiguous.

Single LLM call using the classifier_v1.txt system prompt.  The model returns a
JSON object with scenario_type, confidence, reasoning, and clarification_needed.

Why a separate classifier model (gpt-4o-mini)?
----------------------------------------------
Classification is a simple reasoning task — pick one of three buckets.  gpt-4o-mini
is 10x cheaper than gpt-4o and produces the same quality for structured classification.
The full gpt-4o is reserved for the stage agents that do actual code generation.

Protocol pattern
----------------
RequirementClassifier accepts a _GatewayCallable (not a concrete AIGateway import)
so it can be tested with MagicMock without the openai package installed.
"""

import json
import re
from dataclasses import dataclass
from typing import Literal, Protocol

from orchestrator.gateway.models import GatewayRequest, GatewayResponse
from orchestrator.prompt_builder.loader import PromptLoader

ScenarioType = Literal["greenfield", "brownfield", "ambiguous"]

_CLASSIFIER_MODEL = "gpt-4o-mini"


class _GatewayCallable(Protocol):
    def call(self, request: GatewayRequest) -> GatewayResponse: ...


@dataclass
class ClassifierResult:
    scenario_type: ScenarioType
    confidence: float           # 0.0–1.0
    reasoning: str
    clarification_needed: str | None   # question to ask if ambiguous, else None


class RequirementClassifier:
    """Classifies a requirement string into a pipeline scenario type."""

    def __init__(
        self,
        gateway: _GatewayCallable,
        loader: PromptLoader | None = None,
        model: str = _CLASSIFIER_MODEL,
    ) -> None:
        self._gateway = gateway
        self._loader = loader or PromptLoader()
        self._model = model

    def classify(self, requirement: str, token: str, run_id: str = "") -> ClassifierResult:
        """Classify a requirement and return a ClassifierResult.

        Args:
            requirement: The raw user requirement string.
            token:       Auth token for the gateway call.
            run_id:      Optional run ID for tracing (empty string is safe).

        Returns:
            ClassifierResult with scenario_type, confidence, reasoning.

        Raises:
            ValueError: if the LLM returns malformed or invalid JSON.
        """
        prompt_text, prompt_version = self._loader.load("classifier")
        messages = [
            {"role": "system", "content": prompt_text},
            {"role": "user", "content": requirement},
        ]

        response = self._gateway.call(
            GatewayRequest(
                token=token,
                run_id=run_id or "classify",
                stage_name="classifier",
                messages=messages,
                model=self._model,
                prompt_version=prompt_version,
            )
        )

        return self._parse(response.content or "")

    # ── private ───────────────────────────────────────────────────────────────

    def _parse(self, raw: str) -> ClassifierResult:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"RequirementClassifier: malformed JSON from model: {exc}\n--- raw ---\n{raw}"
            ) from exc

        scenario_type = data.get("scenario_type", "ambiguous")
        if scenario_type not in ("greenfield", "brownfield", "ambiguous"):
            scenario_type = "ambiguous"

        return ClassifierResult(
            scenario_type=scenario_type,
            confidence=float(data.get("confidence", 0.5)),
            reasoning=str(data.get("reasoning", "")),
            clarification_needed=data.get("clarification_needed"),
        )
