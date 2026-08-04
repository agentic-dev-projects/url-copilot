"""
AuditLogger — append-only event log to orch_audit_events.

Every significant state change in an orchestration run produces an audit event.
The log is intentionally append-only: the only write operation is INSERT.  There
is no update() or delete() method on this class — that structural guarantee is
the application-level enforcement of audit immutability.

In production, a database-level trigger would additionally reject any non-INSERT
DML on orch_audit_events, providing tamper-evidence even if a future developer
adds an update path by mistake.  For this prototype the class boundary is
sufficient.

Why a separate class from RunStateStore?
-----------------------------------------
RunStateStore owns orch_runs and orch_stage_results — the business record of what
each stage produced.  AuditLogger owns orch_audit_events — the compliance trail
of who made which decision and when.  Keeping them separate means:

  1. The append-only constraint is enforced at the class boundary.
  2. Any component that makes a decision (gateway, gate, evaluator, memory)
     can import AuditLogger without importing the full state store.
  3. RunStateStore can evolve (add update methods) without risking mutation
     of the audit log.

EventType
---------
EventType is a str Enum so that EventType.STAGE_STARTED == "STAGE_STARTED" is
True.  No .value call is needed when passing to SQL or formatting log messages.
IDE autocomplete prevents typos at the call site.

Session management
------------------
Same pattern as RunStateStore: AuditLogger takes a Session at construction.
Use make_audit_logger() for one-shot operations.  Each log() call commits
immediately so audit events survive crashes that interrupt mid-run processing.

SOC2 compliance note
--------------------
The combination of:
  - Append-only orch_audit_events
  - CHECKPOINT_APPROVED_OVERRIDE event with human justification (Phase 3.5)
  - triggered_by ≠ approved_by (four-eyes, Phase 6)
satisfies SOC2 CC7.2 (change-control with documented approvals and exceptions).
"""

import json
from contextlib import contextmanager
from enum import Enum
from typing import Generator

from sqlalchemy import text
from sqlalchemy.orm import Session

from service.db.session import SessionLocal


class EventType(str, Enum):
    """All valid event_type values for orch_audit_events.

    Inherits str so EventType.STAGE_STARTED == "STAGE_STARTED" without .value.
    """

    # Stage lifecycle
    STAGE_STARTED = "STAGE_STARTED"
    STAGE_COMPLETED = "STAGE_COMPLETED"
    STAGE_FAILED = "STAGE_FAILED"
    STAGE_RETRYING = "STAGE_RETRYING"

    # Human approval gates
    CHECKPOINT_REACHED = "CHECKPOINT_REACHED"
    CHECKPOINT_APPROVED = "CHECKPOINT_APPROVED"
    CHECKPOINT_REJECTED = "CHECKPOINT_REJECTED"
    CHECKPOINT_APPROVED_OVERRIDE = "CHECKPOINT_APPROVED_OVERRIDE"   # approved despite blocking issues

    # AI evaluator
    EVALUATOR_STARTED = "EVALUATOR_STARTED"
    EVALUATOR_COMPLETED = "EVALUATOR_COMPLETED"

    # Run lifecycle
    RUN_STARTED = "RUN_STARTED"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_FAILED = "RUN_FAILED"

    # GitHub PR
    PR_CREATED = "PR_CREATED"
    PR_MERGED = "PR_MERGED"

    # Memory
    MEMORY_WRITTEN = "MEMORY_WRITTEN"

    # Clarification loop (ambiguous scenario)
    CLARIFICATION_ASKED = "CLARIFICATION_ASKED"
    CLARIFICATION_ANSWERED = "CLARIFICATION_ANSWERED"


class AuditLogger:
    """Writes append-only events to orch_audit_events.

    The only public write operation is log() — INSERT only, never UPDATE or DELETE.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def log(
        self,
        run_id: str,
        event_type: str,            # EventType value or raw string
        actor: str = "system",
        stage_name: str | None = None,
        actor_role: str | None = None,
        details: dict | None = None,
    ) -> None:
        """Insert one row into orch_audit_events.

        Args:
            run_id:       FK to orch_runs.id — identifies which run this event belongs to.
            event_type:   One of the EventType constants (or a raw string for custom events).
            actor:        github_login of the human or "system" for automated events.
            stage_name:   Name of the pipeline stage if the event is stage-scoped (None for run-level events).
            actor_role:   RBAC role of the actor at the time of the event (e.g. "TECH_LEAD").
            details:      Arbitrary JSON payload.  Shapes vary per event_type — see architecture doc
                          Section 16 for per-event payload conventions.

        Raises:
            sqlalchemy.exc.IntegrityError: if run_id does not exist in orch_runs
                                           (FK constraint violation).
        """
        self.session.execute(
            text(
                "INSERT INTO orch_audit_events "
                "(run_id, event_type, stage_name, actor, actor_role, details) "
                "VALUES "
                "(:run_id, :event_type, :stage_name, :actor, :actor_role, cast(:details as jsonb))"
            ),
            {
                "run_id": run_id,
                "event_type": str(event_type),
                "stage_name": stage_name,
                "actor": actor,
                "actor_role": actor_role,
                "details": json.dumps(details) if details is not None else None,
            },
        )
        self.session.commit()

    def get_events(self, run_id: str) -> list[dict]:
        """Return all audit events for a run in chronological order.

        Primarily used in tests and the CLI summary view.  Not called by the
        pipeline itself — the pipeline only writes, never reads back the audit log.
        """
        rows = self.session.execute(
            text(
                "SELECT id, run_id, event_type, stage_name, actor, actor_role, "
                "       details, created_at "
                "FROM orch_audit_events "
                "WHERE run_id = :run_id "
                "ORDER BY created_at ASC"
            ),
            {"run_id": run_id},
        ).mappings().all()
        return [dict(r) for r in rows]


# ── module-level convenience ──────────────────────────────────────────────────


@contextmanager
def make_audit_logger() -> Generator[AuditLogger, None, None]:
    """Create an AuditLogger backed by a fresh SessionLocal session.

    Commits on clean exit, rolls back on exception, always closes the session.

    Usage:
        with make_audit_logger() as audit:
            audit.log(run_id, EventType.RUN_STARTED, actor="alice")
    """
    session = SessionLocal()
    try:
        yield AuditLogger(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
