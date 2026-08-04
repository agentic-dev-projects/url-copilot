"""
Integration tests for AuditLogger.

Requires PostgreSQL (docker-compose up -d db).  Tests insert real rows into
orch_audit_events and clean up via the run_id fixture teardown.

Run:
    .venv/bin/python -m pytest orchestrator/tests/test_audit.py -v
"""

import uuid

import pytest
from sqlalchemy import text

from orchestrator.governance.audit import AuditLogger, EventType, make_audit_logger
from orchestrator.state.store import RunStateStore
from service.db.session import SessionLocal


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def run_id():
    return f"test-audit-{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def store(db_session):
    return RunStateStore(db_session)


@pytest.fixture()
def audit(db_session):
    return AuditLogger(db_session)


@pytest.fixture(autouse=True)
def seed_run(store, run_id):
    """Every audit event needs a valid FK into orch_runs."""
    store.create_run(run_id, "Audit test requirement", "greenfield", "alice")
    yield


@pytest.fixture(autouse=True)
def cleanup(db_session, run_id):
    yield
    db_session.execute(text("DELETE FROM orch_audit_events WHERE run_id = :id"), {"id": run_id})
    db_session.execute(text("DELETE FROM orch_stage_results WHERE run_id = :id"), {"id": run_id})
    db_session.execute(text("DELETE FROM orch_runs WHERE id = :id"), {"id": run_id})
    db_session.commit()


# ── EventType tests ───────────────────────────────────────────────────────────


def test_event_type_is_string():
    assert EventType.STAGE_STARTED == "STAGE_STARTED"
    assert EventType.CHECKPOINT_APPROVED_OVERRIDE == "CHECKPOINT_APPROVED_OVERRIDE"
    assert str(EventType.RUN_COMPLETED) == "RUN_COMPLETED"


def test_all_18_event_types_defined():
    expected = {
        "STAGE_STARTED", "STAGE_COMPLETED", "STAGE_FAILED", "STAGE_RETRYING",
        "CHECKPOINT_REACHED", "CHECKPOINT_APPROVED", "CHECKPOINT_REJECTED",
        "CHECKPOINT_APPROVED_OVERRIDE",
        "EVALUATOR_STARTED", "EVALUATOR_COMPLETED",
        "RUN_STARTED", "RUN_COMPLETED", "RUN_FAILED",
        "PR_CREATED", "PR_MERGED",
        "MEMORY_WRITTEN",
        "CLARIFICATION_ASKED", "CLARIFICATION_ANSWERED",
    }
    defined = {e.value for e in EventType}
    assert defined == expected


# ── log() tests ───────────────────────────────────────────────────────────────


def test_log_inserts_row(audit, run_id):
    audit.log(run_id, EventType.RUN_STARTED, actor="alice")
    events = audit.get_events(run_id)
    assert len(events) == 1
    assert events[0]["event_type"] == "RUN_STARTED"
    assert events[0]["actor"] == "alice"
    assert events[0]["run_id"] == run_id


def test_log_three_events_in_order(audit, run_id):
    audit.log(run_id, EventType.RUN_STARTED, actor="alice")
    audit.log(run_id, EventType.STAGE_STARTED, actor="system", stage_name="requirements_analysis")
    audit.log(run_id, EventType.STAGE_COMPLETED, actor="system", stage_name="requirements_analysis")

    events = audit.get_events(run_id)
    assert len(events) == 3
    assert [e["event_type"] for e in events] == [
        "RUN_STARTED", "STAGE_STARTED", "STAGE_COMPLETED"
    ]


def test_log_with_all_optional_fields(audit, run_id):
    audit.log(
        run_id,
        EventType.CHECKPOINT_APPROVED,
        actor="bob",
        stage_name="architecture_design",
        actor_role="TECH_LEAD",
        details={"comment": "LGTM", "ai_score": 4},
    )
    events = audit.get_events(run_id)
    assert len(events) == 1
    row = events[0]
    assert row["stage_name"] == "architecture_design"
    assert row["actor_role"] == "TECH_LEAD"
    assert row["details"]["comment"] == "LGTM"
    assert row["details"]["ai_score"] == 4


def test_log_with_null_optional_fields(audit, run_id):
    audit.log(run_id, EventType.RUN_STARTED, actor="system")
    events = audit.get_events(run_id)
    assert events[0]["stage_name"] is None
    assert events[0]["actor_role"] is None
    assert events[0]["details"] is None


def test_log_details_jsonb_round_trip(audit, run_id):
    payload = {
        "blocking_issues": ["No auth on new endpoint", "Migration not reversible"],
        "justification": "Accepting risk for demo",
        "ai_score": 2,
    }
    audit.log(run_id, EventType.CHECKPOINT_APPROVED_OVERRIDE, actor="carol", details=payload)
    events = audit.get_events(run_id)
    assert events[0]["details"]["blocking_issues"] == payload["blocking_issues"]
    assert events[0]["details"]["justification"] == "Accepting risk for demo"


def test_log_accepts_raw_string_event_type(audit, run_id):
    audit.log(run_id, "CUSTOM_EVENT", actor="system")
    events = audit.get_events(run_id)
    assert events[0]["event_type"] == "CUSTOM_EVENT"


def test_log_stage_lifecycle(audit, run_id):
    for event in [EventType.STAGE_STARTED, EventType.STAGE_RETRYING, EventType.STAGE_COMPLETED]:
        audit.log(run_id, event, actor="system", stage_name="implementation")

    events = audit.get_events(run_id)
    assert len(events) == 3
    assert events[1]["event_type"] == "STAGE_RETRYING"


# ── append-only structural guarantee ─────────────────────────────────────────


def test_audit_logger_has_no_update_or_delete_method():
    """AuditLogger must not expose any mutation path beyond log()."""
    public_methods = [m for m in dir(AuditLogger) if not m.startswith("_")]
    assert "update" not in public_methods
    assert "delete" not in public_methods
    assert "patch" not in public_methods


# ── make_audit_logger context manager ────────────────────────────────────────


def test_make_audit_logger_commits_on_success(run_id):
    with make_audit_logger() as audit:
        audit.log(run_id, EventType.RUN_STARTED, actor="alice")
    # Verify row is visible in a new session
    with make_audit_logger() as verify:
        events = verify.get_events(run_id)
    assert len(events) == 1
    assert events[0]["event_type"] == "RUN_STARTED"
