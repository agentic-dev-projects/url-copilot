"""
Unit tests for MetricsTracker.

All tests use SQLite in-memory — no PostgreSQL required.  The orch_metrics and
orch_stage_results tables are created with CREATE TABLE statements that mirror
the Alembic migration (Phase 2), using SQLite-compatible types.

Run: .venv/bin/python -m pytest orchestrator/tests/test_metrics.py -v
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from orchestrator.metrics.tracker import MetricsTracker, _parse_dt

# ── SQLite test fixtures ──────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS orch_runs (
    id              TEXT PRIMARY KEY,
    requirement     TEXT,
    scenario_type   TEXT,
    status          TEXT NOT NULL DEFAULT 'running',
    triggered_by    TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    completed_at    TEXT,
    pr_url          TEXT,
    feature_branch  TEXT,
    feedback_score  INTEGER,
    feedback_comment TEXT
);

CREATE TABLE IF NOT EXISTS orch_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    stage_name      TEXT NOT NULL,
    trace_id        TEXT,
    tokens_in       INTEGER DEFAULT 0,
    tokens_out      INTEGER DEFAULT 0,
    cost_usd        REAL DEFAULT 0.0,
    llm_latency_ms  INTEGER DEFAULT 0,
    cache_hit       INTEGER DEFAULT 0,
    model_used      TEXT,
    prompt_version  TEXT
);

CREATE TABLE IF NOT EXISTS orch_stage_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    stage_name      TEXT NOT NULL,
    status          TEXT NOT NULL,
    attempt_number  INTEGER DEFAULT 1,
    prompt_version  TEXT,
    model_used      TEXT,
    started_at      TEXT,
    completed_at    TEXT,
    output_artifact TEXT,
    error_message   TEXT
);
"""


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    with engine.connect() as conn:
        for stmt in _DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))
        conn.commit()
    Session_ = sessionmaker(bind=engine)
    sess = Session_()
    yield sess
    sess.close()
    engine.dispose()


def _insert_run(session, run_id: str, status: str = "completed") -> None:
    session.execute(
        text("INSERT INTO orch_runs (id, requirement, status) VALUES (:id, :req, :status)"),
        {"id": run_id, "req": "test requirement", "status": status},
    )
    session.commit()


def _insert_metric(
    session,
    run_id: str,
    stage: str = "architecture_design",
    tokens_in: int = 100,
    tokens_out: int = 50,
    cost_usd: float = 0.001,
    latency_ms: int = 200,
    cache_hit: bool = False,
) -> None:
    session.execute(
        text(
            "INSERT INTO orch_metrics "
            "(run_id, stage_name, tokens_in, tokens_out, cost_usd, llm_latency_ms, cache_hit) "
            "VALUES (:run_id, :stage, :tin, :tout, :cost, :lat, :hit)"
        ),
        {
            "run_id": run_id,
            "stage": stage,
            "tin": tokens_in,
            "tout": tokens_out,
            "cost": cost_usd,
            "lat": latency_ms,
            "hit": 1 if cache_hit else 0,
        },
    )
    session.commit()


def _insert_stage_result(
    session,
    run_id: str,
    stage: str,
    status: str,
    attempt: int = 1,
    started_at: str | None = None,
    completed_at: str | None = None,
) -> None:
    session.execute(
        text(
            "INSERT INTO orch_stage_results "
            "(run_id, stage_name, status, attempt_number, started_at, completed_at) "
            "VALUES (:run_id, :stage, :status, :attempt, :started, :completed)"
        ),
        {
            "run_id": run_id,
            "stage": stage,
            "status": status,
            "attempt": attempt,
            "started": started_at,
            "completed": completed_at,
        },
    )
    session.commit()


# ── summarize() ───────────────────────────────────────────────────────────────


def test_summarize_returns_run_id(session):
    tracker = MetricsTracker(session)
    summary = tracker.summarize("run-001")
    assert summary["run_id"] == "run-001"


def test_summarize_empty_returns_zeros(session):
    tracker = MetricsTracker(session)
    summary = tracker.summarize("run-no-data")
    assert summary["total_cost_usd"] == 0.0
    assert summary["total_tokens"] == 0
    assert summary["avg_llm_latency_ms"] == 0.0
    assert summary["cache_hit_rate"] == 0.0
    assert summary["stages_completed"] == 0
    assert summary["stages_failed"] == 0
    assert summary["retry_count"] == 0


def test_summarize_aggregates_cost_and_tokens(session):
    _insert_metric(session, "run-agg", tokens_in=100, tokens_out=50, cost_usd=0.001)
    _insert_metric(session, "run-agg", tokens_in=200, tokens_out=100, cost_usd=0.002, stage="implementation")

    summary = MetricsTracker(session).summarize("run-agg")
    assert summary["total_tokens"] == 450
    assert abs(summary["total_cost_usd"] - 0.003) < 1e-9


def test_summarize_cache_hit_rate_half(session):
    _insert_metric(session, "run-cache", cache_hit=True)
    _insert_metric(session, "run-cache", cache_hit=False, stage="implementation")

    summary = MetricsTracker(session).summarize("run-cache")
    assert summary["cache_hit_rate"] == 0.5


def test_summarize_all_cache_hits(session):
    _insert_metric(session, "run-all-cache", cache_hit=True)
    _insert_metric(session, "run-all-cache", cache_hit=True, stage="implementation")

    summary = MetricsTracker(session).summarize("run-all-cache")
    assert summary["cache_hit_rate"] == 1.0


def test_summarize_avg_latency(session):
    _insert_metric(session, "run-lat", latency_ms=100)
    _insert_metric(session, "run-lat", latency_ms=300, stage="implementation")

    summary = MetricsTracker(session).summarize("run-lat")
    assert summary["avg_llm_latency_ms"] == 200.0


def test_summarize_stage_counts(session):
    _insert_stage_result(session, "run-stages", "architecture_design", "completed")
    _insert_stage_result(session, "run-stages", "implementation", "completed")
    _insert_stage_result(session, "run-stages", "unit_tests", "failed")

    summary = MetricsTracker(session).summarize("run-stages")
    assert summary["stages_completed"] == 2
    assert summary["stages_failed"] == 1


def test_summarize_retry_count(session):
    # attempt_number=1 failed, attempt_number=2 succeeded → 1 retried stage
    _insert_stage_result(session, "run-retry", "implementation", "failed", attempt=1)
    _insert_stage_result(session, "run-retry", "implementation", "completed", attempt=2)

    summary = MetricsTracker(session).summarize("run-retry")
    assert summary["retry_count"] == 1


def test_summarize_does_not_count_other_run(session):
    _insert_metric(session, "run-a", tokens_in=100, tokens_out=50, cost_usd=0.001)
    _insert_metric(session, "run-b", tokens_in=999, tokens_out=999, cost_usd=9.99)

    summary = MetricsTracker(session).summarize("run-a")
    assert summary["total_tokens"] == 150


# ── compute_mttr() ────────────────────────────────────────────────────────────


def test_compute_mttr_returns_none_with_no_retries(session):
    _insert_stage_result(session, "run-no-retry", "impl", "completed", attempt=1,
                         started_at="2025-08-01 10:00:00", completed_at="2025-08-01 10:05:00")
    assert MetricsTracker(session).compute_mttr("run-no-retry") is None


def test_compute_mttr_returns_none_for_empty_run(session):
    assert MetricsTracker(session).compute_mttr("run-empty") is None


def test_compute_mttr_single_retry(session):
    # Stage failed at 10:05, retried starting at 10:07 → 120 seconds recovery
    _insert_stage_result(session, "run-mttr", "impl", "failed", attempt=1,
                         started_at="2025-08-01 10:00:00",
                         completed_at="2025-08-01 10:05:00")
    _insert_stage_result(session, "run-mttr", "impl", "completed", attempt=2,
                         started_at="2025-08-01 10:07:00",
                         completed_at="2025-08-01 10:12:00")

    mttr = MetricsTracker(session).compute_mttr("run-mttr")
    assert mttr == 120.0


def test_compute_mttr_averages_multiple_retries(session):
    # Stage A: failed→retry gap = 60s
    _insert_stage_result(session, "run-multi", "stage_a", "failed", attempt=1,
                         started_at="2025-08-01 10:00:00",
                         completed_at="2025-08-01 10:01:00")
    _insert_stage_result(session, "run-multi", "stage_a", "completed", attempt=2,
                         started_at="2025-08-01 10:02:00",
                         completed_at="2025-08-01 10:05:00")
    # Stage B: failed→retry gap = 180s
    _insert_stage_result(session, "run-multi", "stage_b", "failed", attempt=1,
                         started_at="2025-08-01 10:10:00",
                         completed_at="2025-08-01 10:11:00")
    _insert_stage_result(session, "run-multi", "stage_b", "completed", attempt=2,
                         started_at="2025-08-01 10:14:00",
                         completed_at="2025-08-01 10:16:00")

    mttr = MetricsTracker(session).compute_mttr("run-multi")
    assert mttr == 120.0  # (60 + 180) / 2


def test_compute_mttr_ignores_other_runs(session):
    _insert_stage_result(session, "run-x", "impl", "failed", attempt=1,
                         completed_at="2025-08-01 10:00:00")
    _insert_stage_result(session, "run-x", "impl", "completed", attempt=2,
                         started_at="2025-08-01 10:10:00")
    # run-y has no retries
    assert MetricsTracker(session).compute_mttr("run-y") is None


# ── success_rate() ────────────────────────────────────────────────────────────


def test_success_rate_empty_table_returns_zero(session):
    assert MetricsTracker(session).success_rate() == 0.0


def test_success_rate_all_completed(session):
    for i in range(4):
        _insert_run(session, f"run-ok-{i}", status="completed")
    assert MetricsTracker(session).success_rate() == 1.0


def test_success_rate_half_completed(session):
    _insert_run(session, "run-ok-1", status="completed")
    _insert_run(session, "run-ok-2", status="completed")
    _insert_run(session, "run-fail-1", status="failed")
    _insert_run(session, "run-fail-2", status="failed")
    assert MetricsTracker(session).success_rate() == 0.5


def test_success_rate_none_completed(session):
    _insert_run(session, "run-fail-a", status="failed")
    _insert_run(session, "run-fail-b", status="running")
    assert MetricsTracker(session).success_rate() == 0.0


# ── _parse_dt() helper ────────────────────────────────────────────────────────


def test_parse_dt_returns_none_for_none():
    assert _parse_dt(None) is None


def test_parse_dt_passthrough_datetime():
    dt = datetime(2025, 8, 1, 10, 0, 0)
    assert _parse_dt(dt) == dt


def test_parse_dt_parses_sqlite_string_with_microseconds():
    result = _parse_dt("2025-08-01 10:05:30.123456")
    assert result == datetime(2025, 8, 1, 10, 5, 30, 123456)


def test_parse_dt_parses_sqlite_string_without_microseconds():
    result = _parse_dt("2025-08-01 10:05:30")
    assert result == datetime(2025, 8, 1, 10, 5, 30)


def test_parse_dt_returns_none_for_unparseable():
    assert _parse_dt("not-a-date") is None
