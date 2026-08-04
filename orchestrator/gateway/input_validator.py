"""
InputValidator — schema validation and prompt injection detection.

Two distinct checks, two distinct exception types:

1. Schema validation (InputValidationError)
   - Requirement text must be 10–2000 characters
   - No null bytes (\x00) — null bytes break some tokenisers and indicate
     binary data accidentally included in a prompt

2. Prompt injection detection (PromptInjectionError)
   - Patterns that attempt to override the system prompt or jailbreak the model
   - Case-insensitive substring match — simple and fast
   - False-positive rate is low because these patterns are rarely in legitimate
     engineering requirements

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

_MIN_LENGTH = 10
_MAX_LENGTH = 2000

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
    """Validates gateway request messages for schema compliance and injection patterns."""

    def validate(self, messages: list[dict]) -> None:
        """Run schema and injection checks on all user-role messages.

        Args:
            messages: OpenAI chat messages list (each dict has 'role' and 'content').

        Raises:
            InputValidationError:  if any message fails schema checks.
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
        if len(text) < _MIN_LENGTH:
            raise InputValidationError(
                f"Requirement is too short ({len(text)} chars). "
                f"Minimum is {_MIN_LENGTH} characters."
            )
        if len(text) > _MAX_LENGTH:
            raise InputValidationError(
                f"Requirement is too long ({len(text)} chars). "
                f"Maximum is {_MAX_LENGTH} characters."
            )

    def _check_injection(self, text: str) -> None:
        lower = text.lower()
        for pattern in _INJECTION_PATTERNS:
            if pattern in lower:
                raise PromptInjectionError(
                    f"Prompt injection pattern detected: '{pattern}'. "
                    f"The requirement must describe an engineering task."
                )
