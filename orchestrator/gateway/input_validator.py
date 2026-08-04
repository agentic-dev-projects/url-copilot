"""
InputValidator — prompt injection detection for gateway messages.

Checks all user-role messages for:
  - Null bytes (\x00) — break some tokenisers, indicate accidental binary data
  - Prompt injection patterns — attempts to override the system prompt

Length validation is NOT done here.  By the time messages reach the gateway
they include Layer 4 injected artifacts from prior stages, making them much
larger than the original user requirement.  Raw requirement length is validated
at the CLI entry point (handle_run in run.py) before planner.plan() is called.

Why substring match instead of regex?
--------------------------------------
The injection patterns are multi-word phrases that don't need regex capture
groups or alternation.  Simple `pattern in text.lower()` is O(n·m), instant
for prompt-length strings, and trivially readable.

Adding new patterns
--------------------
Append to _INJECTION_PATTERNS.  No other code changes needed.
"""

from orchestrator.gateway.models import InputValidationError, PromptInjectionError

_INJECTION_PATTERNS = [
    "ignore previous instructions",
    "ignore your system prompt",
    "disregard all prior",
    "forget everything above",
    "you are now",
    "pretend you are",
    "act as if you have no restrictions",
    "jailbreak",
]


class InputValidator:
    """Validates gateway request messages for injection patterns."""

    def validate(self, messages: list[dict]) -> None:
        """Run injection checks on all user-role messages.

        Args:
            messages: OpenAI chat messages list (each dict has 'role' and 'content').

        Raises:
            InputValidationError:  if any message contains null bytes.
            PromptInjectionError:  if any injection pattern is found.
        """
        for msg in messages:
            content = msg.get("content") or ""
            if msg.get("role") == "user":
                self._check_schema(content)
                self._check_injection(content)

    # ── private ───────────────────────────────────────────────────────────────

    def _check_schema(self, text: str) -> None:
        if "\x00" in text:
            raise InputValidationError(
                "Prompt contains null bytes — possible binary data in input."
            )

    def _check_injection(self, text: str) -> None:
        lower = text.lower()
        for pattern in _INJECTION_PATTERNS:
            if pattern in lower:
                raise PromptInjectionError(
                    f"Prompt injection pattern detected: '{pattern}'. "
                    f"The requirement must describe an engineering task."
                )
