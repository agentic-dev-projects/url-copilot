"""
GuardrailChecker — PII and dangerous code pattern detection.

Two scan directions:

check_input(text)  — runs BEFORE the LLM call
    Blocks prompts that contain PII (SSN, credit card, email address).
    We don't want to send customer data to the OpenAI API.

check_output(text) — runs AFTER the LLM call
    Blocks responses that contain dangerous code patterns or hardcoded secrets.
    The agent is allowed to write code inside service/ — but not shell commands
    that would affect the host OS, SQL destructors, or credentials in plaintext.

Why block PII in input?
-------------------------
Sending PII to an external LLM API creates GDPR/CCPA risk.  The orchestrator
processes engineering requirements — there is no legitimate reason for SSNs,
credit card numbers, or personal emails to appear in a requirement string.

Why regex for PII, substring match for code patterns?
-------------------------------------------------------
PII patterns (SSN: NNN-NN-NNNN) have structure that requires regex.
Code patterns (os.system(), rm -rf) are literal strings — substring match is
faster and more readable.

False positives
----------------
Guardrail checks intentionally err on the side of blocking.  A legitimate
requirement that mentions an email domain (e.g. "parse email headers") would
trip the email regex.  In production, tune the patterns or add an allow-list.
For this prototype, security posture is more important than false-positive rate.
"""

import re

from orchestrator.gateway.models import GuardrailViolationError

# ── Input (PII) patterns ──────────────────────────────────────────────────────
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CREDIT_CARD_RE = re.compile(r"\b(?:\d[ -]?){15,16}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")

# ── Output (code safety) patterns ────────────────────────────────────────────
_DANGEROUS_CODE_PATTERNS = [
    "os.system(",
    "subprocess.",
    "eval(",
    "exec(",
    "__import__(",
    "rm -rf",
    "DROP TABLE",
    "DELETE FROM",           # without WHERE is caught by context — any occurrence is suspect
    "TRUNCATE TABLE",
    "password =",            # hardcoded credential heuristic
    "secret =",
    "api_key =",
    "private_key =",
]


class GuardrailChecker:
    """Scans prompts for PII and LLM responses for dangerous code patterns."""

    def check_input(self, text: str) -> None:
        """Scan input text for PII before sending to the LLM.

        Args:
            text: The concatenated prompt text (all message contents joined).

        Raises:
            GuardrailViolationError: if any PII pattern is detected.
        """
        if _SSN_RE.search(text):
            raise GuardrailViolationError(
                "Input contains what appears to be a US Social Security Number. "
                "Remove PII before submitting a requirement."
            )
        if _CREDIT_CARD_RE.search(text):
            raise GuardrailViolationError(
                "Input contains what appears to be a credit card number. "
                "Remove PII before submitting a requirement."
            )
        if _EMAIL_RE.search(text):
            raise GuardrailViolationError(
                "Input contains an email address. "
                "Requirements must not include personal data."
            )

    def check_output(self, text: str) -> None:
        """Scan LLM output for dangerous code patterns before returning to caller.

        Args:
            text: The full LLM response content string.

        Raises:
            GuardrailViolationError: if any dangerous pattern is found.
        """
        if not text:
            return
        for pattern in _DANGEROUS_CODE_PATTERNS:
            if pattern in text:
                raise GuardrailViolationError(
                    f"Output guardrail triggered: pattern '{pattern}' detected "
                    f"in LLM response. The response has been blocked."
                )
