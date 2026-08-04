"""
orchestrator.gateway — AI Gateway: the single choke point for all LLM calls.

Every request (CLI trigger, stage execution, evaluator call) passes through this
package before any token is sent to OpenAI.  The pipeline enforces auth, RBAC,
rate limiting, input validation, and output guardrails regardless of which stage
or component is making the call.

LangSmith integration
---------------------
LangSmith automatically traces every OpenAI call when these env vars are set:
    LANGCHAIN_TRACING_V2=true
    LANGCHAIN_API_KEY=lsv2_...
    LANGCHAIN_PROJECT=url-copilot

The gateway wraps the OpenAI client with langsmith.wrappers.wrap_openai() at
startup.  From that point every call is traced with zero additional code —
inputs, outputs, token counts, cost, and latency appear in the LangSmith
dashboard automatically.

Because LangSmith handles LLM-level tracing, the gateway's tracer.py and
logger.py are simplified: tracer.py generates a trace_id for correlation
(used in orch_metrics), and logger.py logs only gateway-level events
(auth failures, guardrail violations, rate limit hits) to stdout as JSON.
LLM call details go to LangSmith, not to our logs.

Pre-call pipeline (raises exception to block the request):
  1.  TokenAuthenticator   resolve token → CurrentUser via users.yaml
  2.  RBACCheckpoint       check user has required permission
  3.  TokenBudgetManager   daily token cap per role (reads orch_metrics)
  4.  RateLimiter          per-user calls/minute (in-memory)
  5.  InputValidator       schema check (length, null bytes) + injection patterns
  6.  GuardrailChecker     PII scan + banned operation patterns
  7.  RequestTracer        generate trace_id UUID, start timing span

LLM call:
  8.  OpenAI API call      via wrap_openai() — automatically traced by LangSmith

Post-call:
  9.  GuardrailChecker     scan output for dangerous code patterns / PII
  10. CostTracker          tokens × price → USD, writes to orch_metrics
                           (used for RBAC daily budget enforcement)
  11. RequestTracer        end span, record total latency

Implemented in Phase 6 (auth + RBAC) and Phase 7 (full pipeline).
"""
