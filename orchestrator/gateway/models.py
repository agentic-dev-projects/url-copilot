"""
Shared data models and exceptions for the AI Gateway.

Putting GatewayRequest, GatewayResponse, and all exception types here avoids
circular imports between gateway components.  Any module that needs to build a
request or catch a gateway exception imports from here, not from gateway.py.
"""

from dataclasses import dataclass, field


# ── Request / Response ────────────────────────────────────────────────────────


@dataclass
class GatewayRequest:
    """Input to AIGateway.call().  Carries everything the pipeline needs to
    authenticate, validate, call the LLM, and record the result."""

    token: str                          # raw CLI --token; resolved to CurrentUser by TokenAuthenticator
    run_id: str                         # FK to orch_runs — links this call to an orchestration run
    stage_name: str                     # e.g. "architecture_design", "eval_architecture_design"
    messages: list[dict]                # OpenAI chat messages format
    model: str                          # e.g. "gpt-4o", from models.yaml
    prompt_version: str                 # e.g. "architecture_v1" — stored in orch_metrics for tracing
    tools: list[dict] | None = None     # OpenAI function definitions; None for plain chat calls


@dataclass
class GatewayResponse:
    """Output of AIGateway.call().  Carries the LLM response plus metadata."""

    content: str | None                 # text response (None if model returned only tool_calls)
    tool_calls: list[dict] | None       # structured tool call requests from the model
    usage: dict                         # {"input_tokens": int, "output_tokens": int}
    trace_id: str                       # UUID linking this response to orch_metrics row
    cache_hit: bool = False             # True if response was served from orch_cache (Phase 9)


# ── Gateway exceptions ────────────────────────────────────────────────────────


class RateLimitError(Exception):
    """Raised when a user exceeds the per-minute call limit."""


class TokenBudgetExceededError(Exception):
    """Raised when a user has consumed their daily token budget."""


class InputValidationError(Exception):
    """Raised when the request fails schema validation (length, null bytes)."""


class PromptInjectionError(Exception):
    """Raised when a prompt injection pattern is detected in the input."""


class GuardrailViolationError(Exception):
    """Raised when PII or dangerous code patterns are detected in input or output."""
