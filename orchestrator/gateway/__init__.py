"""
orchestrator.gateway — AI Gateway: the single choke point for all LLM calls.

Every request (CLI trigger, stage execution, evaluator call) passes through this
package before any token is sent to OpenAI.  The 11-layer pre/post-call pipeline:

Pre-call (raises exception to block the request):
  1.  TokenAuthenticator   resolve token → CurrentUser via users.yaml
  2.  RBACCheckpoint       check user has required permission
  3.  TokenBudgetManager   daily token cap per role (reads orch_metrics)
  4.  RateLimiter          per-user calls/minute (in-memory)
  5.  InputValidator       schema check (length, null bytes) + injection patterns
  6.  GuardrailChecker     PII scan + banned operation patterns
  7.  RequestTracer        generate trace_id UUID, start timing span

LLM call:
  8.  OpenAI API call      with function calling (structured output)

Post-call:
  9.  GuardrailChecker     scan output for dangerous code patterns / PII
  10. CostTracker          tokens × price → USD, writes to orch_metrics
  11. StructuredLogger     JSON log line to stdout + RequestTracer end span

Implemented in Phase 6 (auth + RBAC) and Phase 7 (full pipeline).
"""
