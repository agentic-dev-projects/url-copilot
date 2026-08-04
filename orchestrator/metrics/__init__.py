"""
orchestrator.metrics — Run-level observability and cost budgeting.

Observability split between LangSmith and orch_metrics
-------------------------------------------------------
LangSmith (automatic, zero code):
    Every LLM call trace — inputs, outputs, token counts, latency,
    model used, prompt version — visible in the LangSmith dashboard.
    No instrumentation needed; set LANGCHAIN_TRACING_V2=true in .env.

orch_metrics table (our custom tracking):
    Per-call rows written by CostTracker in the gateway after each LLM call.
    Purpose: RBAC daily token budget enforcement (TokenBudgetManager queries
    this table to check if a user has exceeded their daily token cap).
    Also aggregated at run end for the CLI summary printout.

Why keep orch_metrics if LangSmith already tracks tokens?
----------------------------------------------------------
LangSmith is an external SaaS service.  TokenBudgetManager needs to query
token usage in real time to decide whether to allow the next call.  Querying
our own PostgreSQL table is sub-millisecond; calling the LangSmith API for
budget enforcement would add latency and a network dependency to every LLM call.
orch_metrics is the authoritative source for budget decisions; LangSmith is
the observability and debugging surface.

Files
-----
tracker.py    MetricsTracker — summarize(run_id), compute_mttr(run_id).
              Aggregates orch_metrics rows into the end-of-run CLI summary:
              total cost, total tokens, cache hit rate, stages completed, etc.

LangSmith dashboard shows
--------------------------
- Full trace tree for each run (node → LLM call → tool calls → response)
- Token usage per call with cost breakdown
- Prompt version used at each call
- Latency distribution across stages
- Evaluation scores from the hybrid evaluator's o1-mini calls

Implemented in Phase 16 (tracker.py).
"""
