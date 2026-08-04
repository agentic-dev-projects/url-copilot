"""
AIGateway — central choke point for all LLM calls.

Every request (stage execution, evaluator call, classifier call) passes through
AIGateway.call() before any token is sent to OpenAI.  The 11-step pipeline
enforces auth, RBAC, rate limiting, input validation, guardrails, and cost
tracking regardless of which stage is making the call.

LangSmith integration
---------------------
The OpenAI client is wrapped with langsmith.wrappers.wrap_openai() once at
construction.  From that point every API call is automatically traced — inputs,
outputs, token counts, cost, and latency appear in the LangSmith dashboard
with zero additional instrumentation in stage agents or the evaluator.

The LANGCHAIN_TRACING_V2, LANGCHAIN_API_KEY, and LANGCHAIN_PROJECT environment
variables must be set in .env for tracing to activate.  If they are absent,
wrap_openai() is a no-op and calls go directly to OpenAI without tracing.

11-step pipeline
----------------
Pre-call (raises exception to block request):
  1. TokenAuthenticator   resolve token → CurrentUser
  2. RBACCheckpoint       check user has trigger_run permission
  3. TokenBudgetManager   daily token cap check (orch_metrics)
  4. RateLimiter          20 calls/min per user (in-memory)
  5. InputValidator       schema + injection pattern check
  6. GuardrailChecker     PII scan on input
  7. RequestTracer        start timing span, generate trace_id

LLM call:
  8. OpenAI API           via wrap_openai() — auto-traced by LangSmith

Post-call:
  9. GuardrailChecker     dangerous code / PII scan on output
  10. CostTracker         tokens × price → orch_metrics row
  11. RequestTracer       end timing span

Session management
------------------
AIGateway creates one SessionLocal() session per call() invocation for the
DB-touching components (steps 3 and 10).  The session is committed and closed
in a finally block regardless of success or failure.  AIGateway itself is a
long-lived singleton — it should not hold a session across calls.
"""

import os
import time

import openai
from langsmith.wrappers import wrap_openai
from sqlalchemy.orm import Session

from orchestrator.gateway.auth import CurrentUser, TokenAuthenticator
from orchestrator.gateway.cost_tracker import CostTracker
from orchestrator.gateway.guardrails import GuardrailChecker
from orchestrator.gateway.input_validator import InputValidator
from orchestrator.gateway.logger import StructuredLogger
from orchestrator.gateway.models import GatewayRequest, GatewayResponse
from orchestrator.gateway.rate_limiter import RateLimiter
from orchestrator.gateway.token_budget import TokenBudgetManager
from orchestrator.gateway.tracer import RequestTracer
from orchestrator.governance.checkpoint import RBACCheckpoint
from service.db.session import SessionLocal


class AIGateway:
    """Orchestrates all pre/post-call pipeline components for every LLM request."""

    def __init__(self) -> None:
        # Wrap once — all subsequent calls are auto-traced by LangSmith
        self._openai = wrap_openai(openai.OpenAI())

        self._auth = TokenAuthenticator()
        self._rbac = RBACCheckpoint(self._auth)
        self._rate_limiter = RateLimiter()
        self._budget = TokenBudgetManager()
        self._input_validator = InputValidator()
        self._guardrails = GuardrailChecker()
        self._cost_tracker = CostTracker()
        self._tracer = RequestTracer()
        self._logger = StructuredLogger()

    def call(self, request: GatewayRequest) -> GatewayResponse:
        """Run the full 11-step pipeline and return the LLM response.

        Args:
            request: Fully-populated GatewayRequest.

        Returns:
            GatewayResponse with content, tool_calls, usage, trace_id.

        Raises:
            AuthenticationError:       step 1 — unknown token
            AuthorizationError:        step 2 — missing permission
            TokenBudgetExceededError:  step 3 — daily cap hit
            RateLimitError:            step 4 — per-minute limit hit
            InputValidationError:      step 5 — schema violation
            PromptInjectionError:      step 5 — injection pattern
            GuardrailViolationError:   step 6 or 9 — PII / dangerous code
            openai.OpenAIError:        step 8 — API error
        """
        session: Session = SessionLocal()
        trace_id: str = ""
        try:
            # ── Pre-call pipeline ─────────────────────────────────────────────

            # Step 1: Resolve token → CurrentUser
            user: CurrentUser = self._auth.resolve(request.token)

            # Step 2: RBAC — must have trigger_run permission
            self._rbac.check_permission(user, "trigger_run")

            # Step 3: Daily token budget check
            estimated = sum(len(m.get("content") or "") for m in request.messages) // 4
            self._budget.check(user, session, estimated_tokens=estimated)

            # Step 4: Per-minute rate limit
            self._rate_limiter.check(user)

            # Step 5: Schema + injection validation
            self._input_validator.validate(request.messages)

            # Step 6: Input guardrails (PII) — scan only user-role messages.
            # System prompts contain codebase file contents (which legitimately
            # include email addresses in test fixtures, auth schemas, etc.).
            # PII risk is only in user-submitted content, not in our own prompts.
            user_text = " ".join(
                m.get("content") or ""
                for m in request.messages
                if m.get("role") == "user"
            )
            self._guardrails.check_input(user_text)

            # Step 7: Start timing span
            trace_id = self._tracer.start()
            self._logger.log_request(trace_id, user.github_login, user.role, request)

            # ── LLM call ──────────────────────────────────────────────────────

            # Step 8: OpenAI API call with TPM rate-limit retry.
            # OpenAI 429s on tokens-per-minute (TPM) limits are transient —
            # the window resets every 60 s.  We back off up to 3 times before
            # propagating so a single large prompt doesn't abort a long pipeline.
            llm_start = time.monotonic()
            _max_retries = 3
            _backoff = 15.0   # seconds; doubles each attempt
            for _attempt in range(_max_retries + 1):
                try:
                    completion = self._openai.chat.completions.create(
                        model=request.model,
                        messages=request.messages,
                        tools=request.tools or openai.NOT_GIVEN,
                    )
                    break
                except openai.RateLimitError as exc:
                    if _attempt == _max_retries:
                        raise
                    wait = _backoff * (2 ** _attempt)
                    self._logger.log_error(
                        trace_id or "pre-trace",
                        "rate_limit_retry",
                        f"OpenAI TPM rate limit hit (attempt {_attempt + 1}/{_max_retries}). "
                        f"Waiting {wait:.0f}s before retry.",
                        stage_name=request.stage_name,
                    )
                    time.sleep(wait)
            llm_latency_ms = (time.monotonic() - llm_start) * 1000

            message = completion.choices[0].message
            content: str | None = message.content
            tool_calls_raw = message.tool_calls

            tool_calls: list[dict] | None = None
            if tool_calls_raw:
                tool_calls = [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls_raw
                ]

            usage = {
                "input_tokens": completion.usage.prompt_tokens,
                "output_tokens": completion.usage.completion_tokens,
            }

            # ── Post-call pipeline ────────────────────────────────────────────

            # Step 9: Output guardrails
            self._guardrails.check_output(content or "")

            # Step 10: Record cost + tokens to orch_metrics
            self._cost_tracker.record(
                session=session,
                trace_id=trace_id,
                run_id=request.run_id,
                stage_name=request.stage_name,
                model=request.model,
                prompt_version=request.prompt_version,
                usage=usage,
                llm_latency_ms=llm_latency_ms,
                github_login=user.github_login,
            )

            # Step 11: End timing span
            duration_ms = self._tracer.end(trace_id)
            response = GatewayResponse(
                content=content,
                tool_calls=tool_calls,
                usage=usage,
                trace_id=trace_id,
            )
            self._logger.log_response(trace_id, request, response, duration_ms)
            return response

        except Exception:
            if trace_id:
                self._tracer.end(trace_id)
            raise
        finally:
            session.close()
