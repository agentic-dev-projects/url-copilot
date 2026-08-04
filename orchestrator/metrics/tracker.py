"""
MetricsTracker — read-only analytics over orch_metrics, orch_stage_results, orch_runs.

Why a separate MetricsTracker instead of querying these tables directly?
------------------------------------------------------------------------
CostTracker owns writes (one row per LLM call).  MetricsTracker owns reads
(aggregation after a run).  Single-responsibility: neither class knows about
the other's SQL.

Why not use LangSmith for this?
--------------------------------
LangSmith traces every LLM call automatically and is the right tool for
debugging individual calls.  But MTTR, stage retry counts, and run-level
success rate are business metrics specific to our orchestrator — they're
easier to compute from our own orch_ tables than to re-derive from LangSmith.
TokenBudgetManager already writes to orch_metrics per call, so the data is
already there.

Cross-database compatibility
----------------------------
MTTR is computed in Python (datetime arithmetic) rather than SQL so that
tests can use SQLite in-memory without PostgreSQL interval functions.
summarize() aggregates counts and sums via portable SQL (no Postgres-specific
functions), so the same queries run on SQLite 3.24+ and PostgreSQL.

Context manager
---------------
Use make_metrics_tracker() for one-shot CLI queries:

    with make_metrics_tracker() as tracker:
        summary = tracker.summarize(run_id)
        print(summary)

For tests, inject a session directly:

    tracker = MetricsTracker(session)
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

from sqlalchemy import text
from sqlalchemy.orm import Session

from service.db.session import SessionLocal


class MetricsTracker:
    """Aggregates orch_metrics and orch_stage_results for a completed run."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ── public API ────────────────────────────────────────────────────────────

    def summarize(self, run_id: str) -> dict:
        """Aggregate all orch_metrics rows for a run into a summary dict.

        Returns a dict with keys:
            run_id             str  — the run_id passed in
            total_cost_usd     float — sum of cost_usd across all LLM calls
            total_tokens       int   — sum of tokens_in + tokens_out
            cache_hit_rate     float — fraction of calls that were cache hits (0.0–1.0)
            avg_llm_latency_ms float — average llm_latency_ms across all calls
            stages_completed   int   — count of orch_stage_results rows with status='completed'
            stages_failed      int   — count of orch_stage_results rows with status='failed'
            retry_count        int   — stages that have attempt_number > 1

        Returns all-zero values (run_id still populated) when no rows exist yet.
        """
        metrics = self._aggregate_metrics(run_id)
        stage_counts = self._aggregate_stage_counts(run_id)
        return {
            "run_id": run_id,
            **metrics,
            **stage_counts,
        }

    def compute_mttr(self, run_id: str) -> float | None:
        """Mean Time To Recovery — average seconds from stage failure to next success.

        Fetches all orch_stage_results rows for the run (all attempts, not just
        the latest), then pairs each failed attempt with the next attempt of the
        same stage.  Returns the average gap in seconds, or None if no retries
        occurred (no stage failed more than once).

        Returns:
            float: average recovery time in seconds, or None if no retries.
        """
        rows = self.session.execute(
            text(
                "SELECT stage_name, attempt_number, status, started_at, completed_at "
                "FROM orch_stage_results "
                "WHERE run_id = :run_id "
                "ORDER BY stage_name, attempt_number ASC"
            ),
            {"run_id": run_id},
        ).mappings().all()

        recovery_times: list[float] = []

        by_stage: dict[str, list[dict]] = {}
        for row in rows:
            stage = row["stage_name"]
            by_stage.setdefault(stage, []).append(dict(row))

        for attempts in by_stage.values():
            for i in range(len(attempts) - 1):
                current = attempts[i]
                nxt = attempts[i + 1]
                if current["status"] == "failed":
                    failed_at = _parse_dt(current["completed_at"])
                    retry_start = _parse_dt(nxt["started_at"])
                    if failed_at is not None and retry_start is not None:
                        recovery_times.append((retry_start - failed_at).total_seconds())

        if not recovery_times:
            return None
        return round(sum(recovery_times) / len(recovery_times), 3)

    def success_rate(self) -> float:
        """Fraction of all orch_runs that completed successfully.

        Returns:
            float 0.0–1.0, or 0.0 if the table is empty.
        """
        row = self.session.execute(
            text(
                "SELECT "
                "  COUNT(*) AS total, "
                "  SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed "
                "FROM orch_runs"
            )
        ).mappings().one()
        total = row["total"] or 0
        if total == 0:
            return 0.0
        return round((row["completed"] or 0) / total, 4)

    # ── private helpers ───────────────────────────────────────────────────────

    def _aggregate_metrics(self, run_id: str) -> dict:
        row = self.session.execute(
            text(
                "SELECT "
                "  COALESCE(SUM(cost_usd), 0)                                AS total_cost_usd, "
                "  COALESCE(SUM(tokens_in + tokens_out), 0)                  AS total_tokens, "
                "  COALESCE(AVG(llm_latency_ms), 0.0)                        AS avg_llm_latency_ms, "
                "  COALESCE(SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END), 0)   AS cache_hits, "
                "  COUNT(*)                                                   AS call_count "
                "FROM orch_metrics "
                "WHERE run_id = :run_id"
            ),
            {"run_id": run_id},
        ).mappings().one()

        call_count = row["call_count"] or 0
        cache_hit_rate = (
            round((row["cache_hits"] or 0) / call_count, 4) if call_count > 0 else 0.0
        )

        return {
            "total_cost_usd": round(float(row["total_cost_usd"] or 0), 6),
            "total_tokens": int(row["total_tokens"] or 0),
            "avg_llm_latency_ms": round(float(row["avg_llm_latency_ms"] or 0), 2),
            "cache_hit_rate": cache_hit_rate,
        }

    def _aggregate_stage_counts(self, run_id: str) -> dict:
        rows = self.session.execute(
            text(
                "SELECT stage_name, attempt_number, status "
                "FROM orch_stage_results "
                "WHERE run_id = :run_id"
            ),
            {"run_id": run_id},
        ).mappings().all()

        completed = sum(1 for r in rows if r["status"] == "completed")
        failed = sum(1 for r in rows if r["status"] == "failed")
        # A stage counts as retried if any of its attempt_number values > 1
        retried_stages = {r["stage_name"] for r in rows if (r["attempt_number"] or 1) > 1}

        return {
            "stages_completed": completed,
            "stages_failed": failed,
            "retry_count": len(retried_stages),
        }


# ── helpers ────────────────────────────────────────────────────────────────────


def _parse_dt(value) -> datetime | None:
    """Parse a datetime value that may be a datetime object or an ISO string (SQLite)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    # SQLite stores datetimes as strings: "2025-08-03 12:34:56.123456"
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            continue
    return None


# ── context manager ───────────────────────────────────────────────────────────


@contextmanager
def make_metrics_tracker() -> Generator["MetricsTracker", None, None]:
    """Context manager that opens a DB session and yields a MetricsTracker.

    Usage:
        with make_metrics_tracker() as tracker:
            summary = tracker.summarize(run_id)
    """
    session = SessionLocal()
    try:
        yield MetricsTracker(session)
    finally:
        session.close()
