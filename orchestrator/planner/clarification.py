"""
ClarificationLoop — interactive Q&A that resolves an ambiguous requirement.

When RequirementClassifier returns "ambiguous", the Planner runs this loop
before creating the OrchestratorState and handing off to the engine.  The
result is a resolved_requirement string and an assumptions list that feed
directly into OrchestratorState.

Two-call design
---------------
Call 1 (generate_questions): gpt-4o-mini reads the raw requirement and returns
    up to MAX_QUESTIONS clarifying questions as JSON.

Call 2 (resolve_requirement): after the user answers each question, gpt-4o-mini
    synthesises the original requirement + all Q&A pairs into a single, scoped,
    unambiguous requirement string plus a list of assumption strings.

ask_fn injection
----------------
ClarificationLoop accepts an optional ask_fn callback with signature
    (question: str) -> str
This defaults to the built-in input() for CLI use and is replaced with a
MagicMock in tests — no stdin interaction needed.

Protocol pattern
----------------
Same _GatewayCallable Protocol as classifier.py so tests mock the gateway
without needing the openai package installed.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Protocol

from orchestrator.gateway.models import GatewayRequest, GatewayResponse

MAX_QUESTIONS = 4
_CLARIFY_MODEL = "gpt-4o-mini"

_QUESTIONS_SYSTEM = """\
You are a software requirement analyst.  Your job is to identify ambiguities in
a feature request and produce up to {max_q} concise clarifying questions.

Respond with ONLY valid JSON — no prose, no markdown fences:
{{"questions": ["question 1", "question 2"]}}

Rules:
- At most {max_q} questions.  Fewer is fine if the requirement is mostly clear.
- Each question targets a single, concrete ambiguity (scope, data model, auth, etc.).
- Do not ask about implementation details the engineering team should decide.
""".format(max_q=MAX_QUESTIONS)

_RESOLVE_SYSTEM = """\
You are a software requirement analyst.  A user provided a feature request and
answered {n} clarifying questions.  Synthesise everything into:

1. resolved_requirement — a single, unambiguous requirement sentence or short
   paragraph that an engineer can implement without further questions.
2. assumptions — a list of strings, one per decision made during clarification.

Respond with ONLY valid JSON — no prose, no markdown fences:
{{
  "resolved_requirement": "...",
  "assumptions": ["assumption 1", "assumption 2"]
}}
"""


class _GatewayCallable(Protocol):
    def call(self, request: GatewayRequest) -> GatewayResponse: ...


@dataclass
class ClarificationResult:
    resolved_requirement: str
    assumptions: list[str] = field(default_factory=list)


class ClarificationLoop:
    """Runs a CLI Q&A loop to resolve an ambiguous requirement."""

    def __init__(
        self,
        gateway: _GatewayCallable,
        ask_fn: Callable[[str], str] | None = None,
        model: str = _CLARIFY_MODEL,
    ) -> None:
        self._gateway = gateway
        self._ask_fn = ask_fn or input
        self._model = model

    def run(self, requirement: str, token: str, run_id: str = "") -> ClarificationResult:
        """Ask up to MAX_QUESTIONS questions, collect answers, return resolved result.

        Args:
            requirement: The raw ambiguous requirement string.
            token:       Auth token for gateway calls.
            run_id:      Optional run ID for tracing.

        Returns:
            ClarificationResult(resolved_requirement, assumptions).

        Raises:
            ValueError: if either LLM response is malformed JSON.
        """
        questions = self._generate_questions(requirement, token, run_id)
        qa_pairs = self._collect_answers(questions)
        return self._resolve(requirement, qa_pairs, token, run_id)

    # ── private ───────────────────────────────────────────────────────────────

    def _generate_questions(self, requirement: str, token: str, run_id: str) -> list[str]:
        response = self._gateway.call(
            GatewayRequest(
                token=token,
                run_id=run_id or "clarify-questions",
                stage_name="clarification_questions",
                messages=[
                    {"role": "system", "content": _QUESTIONS_SYSTEM},
                    {"role": "user", "content": requirement},
                ],
                model=self._model,
                prompt_version="questions_v1",
            )
        )
        return self._parse_questions(response.content or "")

    def _collect_answers(self, questions: list[str]) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for q in questions:
            answer = self._ask_fn(f"\nClarification needed:\n  {q}\nYour answer: ")
            pairs.append((q, answer.strip()))
        return pairs

    def _resolve(
        self,
        requirement: str,
        qa_pairs: list[tuple[str, str]],
        token: str,
        run_id: str,
    ) -> ClarificationResult:
        qa_text = "\n".join(
            f"Q{i+1}: {q}\nA{i+1}: {a}" for i, (q, a) in enumerate(qa_pairs)
        )
        user_message = f"Original requirement:\n{requirement}\n\nQ&A:\n{qa_text}"

        response = self._gateway.call(
            GatewayRequest(
                token=token,
                run_id=run_id or "clarify-resolve",
                stage_name="clarification_resolve",
                messages=[
                    {
                        "role": "system",
                        "content": _RESOLVE_SYSTEM.format(n=len(qa_pairs)),
                    },
                    {"role": "user", "content": user_message},
                ],
                model=self._model,
                prompt_version="resolve_v1",
            )
        )
        return self._parse_resolution(response.content or "")

    def _parse_questions(self, raw: str) -> list[str]:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"ClarificationLoop: malformed JSON (questions): {exc}\n--- raw ---\n{raw}"
            ) from exc
        questions = data.get("questions", [])
        return [str(q) for q in questions[:MAX_QUESTIONS]]

    def _parse_resolution(self, raw: str) -> ClarificationResult:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"ClarificationLoop: malformed JSON (resolution): {exc}\n--- raw ---\n{raw}"
            ) from exc

        return ClarificationResult(
            resolved_requirement=str(data.get("resolved_requirement", "")),
            assumptions=[str(a) for a in data.get("assumptions", [])],
        )
