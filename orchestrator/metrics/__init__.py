"""
orchestrator.metrics — Run-level observability aggregation.

Overview
--------
Individual metrics (tokens, cost, latency, cache hits) are written
incrementally after each LLM call by the AI Gateway's CostTracker
(to orch_metrics table).  This package aggregates those per-call rows
into meaningful run-level summaries.

Tracked per call (written by CostTracker in orchestrator.gateway)
------------------------------------------------------------------
tokens_in / tokens_out     Prompt and completion token counts from OpenAI response.
cost_usd                   Calculated as tokens × price from models.yaml price table.
llm_latency_ms             Time from gateway.call() to response received.
tool_latency_ms            Time for each tool execution in ToolRegistry.
cache_hit                  True if response was served from orch_cache (no OpenAI call).
model_used                 Which model served this call (gpt-4o, gpt-4o-mini, o1-mini).
prompt_version             Which prompt file version was used (e.g., architecture_v1).

Derived metrics (computed by MetricsTracker at run end)
-------------------------------------------------------
total_cost_usd             Sum of all cost_usd for the run.
total_tokens               Sum of tokens_in + tokens_out.
cache_hit_rate             Fraction of calls served from cache.
avg_llm_latency_ms         Average LLM call latency across the run.
stages_completed           Count of stages with status=completed.
retry_count                Total stage retry attempts across the run.
mttr                       Mean time to recovery: avg time from STAGE_FAILED
                           to next STAGE_COMPLETED on the same stage (retries).
success_rate               Across all runs: completed / total runs.

Files
-----
tracker.py    MetricsTracker — summarize(run_id), compute_mttr(run_id), success_rate().

Implemented in Phase 16.
"""
