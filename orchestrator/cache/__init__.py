"""
orchestrator.cache — Three-layer caching strategy.

Layer 1 — OpenAI Prompt Cache (zero code required)
----------------------------------------------------
Prompt Builder places the static system prompt (Layer 1) and codebase context
(Layer 2) at the very start of every message.  These two layers are identical
for all LLM calls within a single run, so OpenAI's server-side prompt caching
automatically reuses the KV cache for those tokens.  Expected savings: ~50%
token cost reduction on repeated calls within the same run.

Layer 2 — Response Cache (response_cache.py, PostgreSQL-backed)
----------------------------------------------------------------
Key:     SHA-256(full_prompt_text + model_name)
Storage: orch_cache table (persistent across restarts and reruns)
TTL:     24 hours (set at INSERT time as expires_at = now() + 24h)
Use:     When a failed stage is retried with identical inputs, the response
         cache returns the prior answer instantly without an OpenAI call.
Hit:     Increments orch_cache.hit_count and records cache_hit=True in orch_metrics.

Layer 3 — Tool Result Cache (tool_cache.py, in-memory per run)
---------------------------------------------------------------
Key:     SHA-256(tool_name + json(args, sort_keys=True))
Storage: RunContext.tool_cache (Python dict, evicted when run ends)
Use:     If the same file is read twice in one run (e.g., both architecture
         and implementation stages read service/config.py), the second call
         returns the cached content — no filesystem hit.

Design decision: why not Redis for the response cache?
------------------------------------------------------
Redis is already in the stack (used by the service layer for rate limiting).
However, the orchestrator's response cache needs persistence across process
restarts (reruns of failed stages happen minutes or hours later).  Redis TTL
eviction is non-deterministic under memory pressure.  PostgreSQL's orch_cache
table gives us a deterministic, persistent, queryable cache with no additional
infrastructure dependency.

Implemented in Phase 9.
"""
