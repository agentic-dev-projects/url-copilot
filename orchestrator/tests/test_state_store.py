"""
Integration tests for RunStateStore.

These tests hit the real PostgreSQL database — they cannot use SQLite because
the orch_ tables use JSONB columns which are PostgreSQL-specific.

Each test is self-contained: it creates rows under a unique run_id prefix and
deletes them in a finally block so the DB is left clean regardless of pass/fail.

Run with:
    pytest orchestrator/tests/test_state_store.py -v

Skip with:
    pytest service/tests/ -v       # service tests use SQLite, no DB needed

Requirements:
    DATABASE_URL must point to a PostgreSQL instance with orch_ tables migrated
    (python -m alembic upgrade head).
"""

import uuid
from datetime import datetime, timezone

import pytest

from orchestrator.core.stage import StageNode, StageResult, StageStatus
from orchestrator.state.store import RunStateStore, make_store
from service.db.session import SessionLocal


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def db_session():
    """Yield a real PostgreSQL session; always close at teardown."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def store(db_session):
    """Yield a RunStateStore backed by the test session."""
    return RunStateStore(db_session)


@pytest.fixture()
def run_id():
    """Unique run_id per test so parallel tests don't collide."""
    return f"test-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def cleanup(db_session, run_id):
    """Delete all orch_ rows for this test's run_id after the test completes."""
    yield
    # Delete child rows first (FK constraint order)
    db_session.execute(
        __import__("sqlalchemy").text(
            "DELETE FROM orch_stage_results WHERE run_id = :id"
        ),
        {"id": run_id},
    )
    db_session.execute(
        __import__("sqlalchemy").text(
            "DELETE FROM orch_audit_events WHERE run_id = :id"
        ),
        {"id": run_id},
    )
    db_session.execute(
        __import__("sqlalchemy").text(
            "DELETE FROM orch_metrics WHERE run_id = :id"
        ),
        {"id": run_id},
    )
    db_session.execute(
        __import__("sqlalchemy").text(
            "DELETE FROM orch_memory WHERE source_run_id = :id"
        ),
        {"id": run_id},
    )
    db_session.execute(
        __import__("sqlalchemy").text("DELETE FROM orch_runs WHERE id = :id"),
        {"id": run_id},
    )
    db_session.commit()


# ── orch_runs tests ───────────────────────────────────────────────────────────


def test_create_run_inserts_row(store, run_id):
    store.create_run(run_id, "Add QR code endpoint", "greenfield", "alice")
    run = store.get_run(run_id)
    assert run["id"] == run_id
    assert run["requirement"] == "Add QR code endpoint"
    assert run["scenario_type"] == "greenfield"
    assert run["triggered_by"] == "alice"
    assert run["status"] == "running"
    assert run["completed_at"] is None


def test_get_run_raises_for_unknown_id(store):
    with pytest.raises(KeyError, match="not found"):
        store.get_run("does-not-exist-xyz")


def test_update_run_status(store, run_id):
    store.create_run(run_id, "Test requirement", "brownfield", "alice")
    store.update_run_status(run_id, "paused")
    run = store.get_run(run_id)
    assert run["status"] == "paused"


def test_update_run_completed_sets_fields(store, run_id):
    store.create_run(run_id, "Test requirement", "greenfield", "alice")
    store.update_run_completed(run_id, feedback_score=4, feedback_comment="Excellent work")
    run = store.get_run(run_id)
    assert run["status"] == "completed"
    assert run["completed_at"] is not None
    assert run["feedback_score"] == 4
    assert run["feedback_comment"] == "Excellent work"


def test_update_run_pr_sets_fields(store, run_id):
    store.create_run(run_id, "Test requirement", "greenfield", "alice")
    store.update_run_pr(run_id, "https://github.com/org/repo/pull/42", "feat/qr-code")
    run = store.get_run(run_id)
    assert run["pr_url"] == "https://github.com/org/repo/pull/42"
    assert run["feature_branch"] == "feat/qr-code"


# ── orch_stage_results tests ──────────────────────────────────────────────────


def _make_stage_result(stage_name: str, attempt: int = 1, artifact: dict | None = None) -> StageResult:
    return StageResult(
        stage_name=stage_name,
        status=StageStatus.COMPLETED,
        attempt_number=attempt,
        started_at=datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 8, 3, 12, 1, 0, tzinfo=timezone.utc),
        output_artifact=artifact or {"endpoints": ["/api/v1/urls"]},
        prompt_version="architecture_v1",
        model_used="gpt-4o",
    )


def test_save_and_get_stage_result(store, run_id):
    store.create_run(run_id, "Add QR code", "greenfield", "alice")
    result = _make_stage_result("architecture_design")
    store.save_stage_result(result, run_id)

    row = store.get_stage_result(run_id, "architecture_design")
    assert row is not None
    assert row["stage_name"] == "architecture_design"
    assert row["status"] == "completed"
    assert row["attempt_number"] == 1
    assert row["model_used"] == "gpt-4o"
    assert row["prompt_version"] == "architecture_v1"
    # JSONB round-trip
    assert row["output_artifact"]["endpoints"] == ["/api/v1/urls"]


def test_get_stage_result_returns_none_when_not_run(store, run_id):
    store.create_run(run_id, "Add QR code", "greenfield", "alice")
    assert store.get_stage_result(run_id, "architecture_design") is None


def test_get_stage_result_returns_latest_attempt(store, run_id):
    store.create_run(run_id, "Add QR code", "greenfield", "alice")
    attempt1 = _make_stage_result("architecture_design", attempt=1, artifact={"version": 1})
    attempt2 = _make_stage_result("architecture_design", attempt=2, artifact={"version": 2})
    store.save_stage_result(attempt1, run_id)
    store.save_stage_result(attempt2, run_id)

    row = store.get_stage_result(run_id, "architecture_design")
    assert row["attempt_number"] == 2
    assert row["output_artifact"]["version"] == 2


def test_save_stage_result_null_artifact(store, run_id):
    store.create_run(run_id, "Add QR code", "greenfield", "alice")
    result = StageResult(
        stage_name="requirements_analysis",
        status=StageStatus.FAILED,
        attempt_number=1,
        started_at=datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc),
        error_message="LLM timeout",
    )
    store.save_stage_result(result, run_id)
    row = store.get_stage_result(run_id, "requirements_analysis")
    assert row["status"] == "failed"
    assert row["error_message"] == "LLM timeout"
    assert row["output_artifact"] is None


def test_get_all_stage_results_returns_latest_per_stage(store, run_id):
    store.create_run(run_id, "Add QR code", "greenfield", "alice")
    store.save_stage_result(_make_stage_result("requirements_analysis", 1), run_id)
    store.save_stage_result(_make_stage_result("architecture_design", 1), run_id)
    store.save_stage_result(_make_stage_result("architecture_design", 2), run_id)  # retry

    rows = store.get_all_stage_results(run_id)
    stage_names = {r["stage_name"] for r in rows}
    assert stage_names == {"requirements_analysis", "architecture_design"}

    arch_row = next(r for r in rows if r["stage_name"] == "architecture_design")
    assert arch_row["attempt_number"] == 2  # only the latest attempt


# ── load_run_state tests ──────────────────────────────────────────────────────


def test_load_run_state_reconstructs_state(store, run_id):
    store.create_run(run_id, "Add QR code", "greenfield", "alice")
    store.save_stage_result(
        _make_stage_result("requirements_analysis", artifact={"scope": "new endpoint"}),
        run_id,
    )

    state = store.load_run_state(run_id)
    assert state["run_id"] == run_id
    assert state["requirement"] == "Add QR code"
    assert state["scenario_type"] == "greenfield"
    assert state["triggered_by"] == "alice"
    assert state["stage_artifacts"]["requirements_analysis"]["scope"] == "new endpoint"


# ── make_store context manager ────────────────────────────────────────────────


def test_make_store_commits_on_success(run_id):
    with make_store() as store:
        store.create_run(run_id, "Context manager test", "greenfield", "alice")
    # Verify row is visible in a separate session
    with make_store() as verify_store:
        run = verify_store.get_run(run_id)
    assert run["requirement"] == "Context manager test"


def test_make_store_rolls_back_on_exception(run_id):
    try:
        with make_store() as store:
            store.create_run(run_id, "Should roll back", "greenfield", "alice")
            raise RuntimeError("simulated failure")
    except RuntimeError:
        pass
    # Row should not exist — transaction was rolled back
    with make_store() as verify_store:
        with pytest.raises(KeyError):
            verify_store.get_run(run_id)
