"""
CostTracker — records per-call token usage and estimated USD cost to orch_metrics.

Why track cost in our own table when LangSmith already does it?
----------------------------------------------------------------
LangSmith calculates cost automatically and displays it in the dashboard.
However, TokenBudgetManager enforces daily token caps in real-time — it needs
to query today's token usage BEFORE each LLM call to decide whether to allow
the request.  Querying the LangSmith API for this would add network latency
and a hard dependency on an external SaaS to every single LLM call.

orch_metrics is the authoritative source for budget decisions (milliseconds,
in the same DB).  LangSmith is the observability and debugging surface.

Price table
-----------
Prices are approximate as of 2025.  Update the table when OpenAI changes pricing.
All prices are per 1,000 tokens.

Model              Input (per 1K)   Output (per 1K)
gpt-4o             $0.0025          $0.0100
gpt-4o-mini        $0.00015         $0.00060
o1-mini            $0.0030          $0.0120

Unknown models fall back to gpt-4o pricing (safe over-estimate).
"""

import json

from sqlalchemy import text
from sqlalchemy.orm import Session

_PRICE_PER_1K: dict[str, dict[str, float]] = {
    "gpt-4o":      {"input": 0.0025,   "output": 0.0100},
    "gpt-4o-mini": {"input": 0.00015,  "output": 0.00060},
    "o1-mini":     {"input": 0.0030,   "output": 0.0120},
}
_FALLBACK_PRICE = _PRICE_PER_1K["gpt-4o"]


class CostTracker:
    """Calculates USD cost and writes one orch_metrics row per LLM call."""

    def record(
        self,
        *,
        session: Session,
        trace_id: str,
        run_id: str,
        stage_name: str,
        model: str,
        prompt_version: str,
        usage: dict,            # {"input_tokens": int, "output_tokens": int}
        llm_latency_ms: float,
        github_login: str,      # for audit / future user-level rollup
        cache_hit: bool = False,
    ) -> None:
        """Insert one row into orch_metrics.

        Args:
            session:        SQLAlchemy session — caller manages commit.
            trace_id:       UUID from RequestTracer.start().
            run_id:         FK to orch_runs.
            stage_name:     e.g. "architecture_design".
            model:          e.g. "gpt-4o" — used for price lookup.
            prompt_version: e.g. "architecture_v1".
            usage:          {"input_tokens": int, "output_tokens": int}.
            llm_latency_ms: Wall-clock time of the OpenAI API call.
            github_login:   Approving user (stored for attribution; not a FK).
            cache_hit:      True if response came from orch_cache.
        """
        tokens_in = usage.get("input_tokens", 0)
        tokens_out = usage.get("output_tokens", 0)
        cost_usd = self._calculate_cost(model, tokens_in, tokens_out)

        session.execute(
            text(
                "INSERT INTO orch_metrics "
                "(run_id, stage_name, trace_id, tokens_in, tokens_out, cost_usd, "
                " llm_latency_ms, cache_hit, model_used, prompt_version) "
                "VALUES "
                "(:run_id, :stage, :trace_id, :tokens_in, :tokens_out, :cost_usd, "
                " :latency_ms, :cache_hit, :model, :prompt_ver)"
            ),
            {
                "run_id": run_id,
                "stage": stage_name,
                "trace_id": trace_id,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": cost_usd,
                "latency_ms": int(llm_latency_ms),
                "cache_hit": cache_hit,
                "model": model,
                "prompt_ver": prompt_version,
            },
        )
        session.commit()

    # ── private ───────────────────────────────────────────────────────────────

    def _calculate_cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        prices = _PRICE_PER_1K.get(model, _FALLBACK_PRICE)
        return round(
            (tokens_in / 1000) * prices["input"]
            + (tokens_out / 1000) * prices["output"],
            6,
        )
