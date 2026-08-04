"""
RunStateStore — PostgreSQL reads and writes for orch_runs and orch_stage_results.

Each public method uses SQLAlchemy text() queries against the tables created in
the Phase 2 Alembic migration (revision 3a8f1c2d4e5b).  No ORM models are defined
for orch_ tables — raw SQL is simpler and keeps the orchestrator fully decoupled
from the service ORM layer.

Session management
------------------
RunStateStore takes a Session at construction time (dependency injection).

For one-shot CLI and engine operations use the module-level context manager:

    with make_store() as store:
        store.create_run(run_id, requirement, scenario_type, triggered_by)

For tests, pass a session directly so the test controls commit/rollback:

    store = RunStateStore(test_session)
    store.create_run("test-001", ...)

Each write method commits immediately so callers see consistent state without
needing to manage the commit cycle.  The context manager provides an outer
rollback safety net for unexpected exceptions.

Relationship to LangGraph PostgresSaver
----------------------------------------
LangGraph's PostgresSaver checkpoints the full OrchestratorState TypedDict after
every node transition.  That checkpoint is for graph resumption — LangGraph uses
it to restore the exact serialised state when graph.invoke() is called with the
same thread_id after a crash or interrupt().

orch_runs and orch_stage_results are the BUSINESS record:
  - Which requirement triggered the run?
  - Who triggered it?
  - What PR was created?
  - What did each stage actually produce?

The CLI `approve` command reads orch_runs to look up who triggered a run before
verifying the four-eyes constraint.  MetricsTracker reads orch_stage_results to
compute MTTR.  These are different concerns from the technical resume state.

Why synchronous DB calls?
--------------------------
The LangGraph pipeline nodes are synchronous Python functions.  Async DB calls
would require asyncio event loop management in every node, adding complexity with
no throughput benefit — the pipeline is not high-concurrency.
"""

import json
from contextlib import contextmanager
from typing import Any, Generator

from sqlalchemy import text
from sqlalchemy.orm import Session

from orchestrator.core.stage import StageResult
from orchestrator.core.state import OrchestratorState
from service.db.session import SessionLocal


class RunStateStore:
    """Reads and writes orch_runs and orch_stage_results via SQLAlchemy text() queries."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ── orch_runs ──────────────────────────────────────────────────────────

    def create_run(
        self,
        run_id: str,
        requirement: str,
        scenario_type: str,
        triggered_by: str,
    ) -> None:
        """Insert a new orch_runs row with status='running'."""
        self.session.execute(
            text(
                "INSERT INTO orch_runs "
                "(id, requirement, scenario_type, status, triggered_by) "
                "VALUES (:id, :req, :scenario, 'running', :triggered_by)"
            ),
            {
                "id": run_id,
                "req": requirement,
                "scenario": scenario_type,
                "triggered_by": triggered_by,
            },
        )
        self.session.commit()

    def update_run_scenario(self, run_id: str, scenario_type: str) -> None:
        """Update orch_runs.scenario_type once classification has completed."""
        self.session.execute(
            text("UPDATE orch_runs SET scenario_type = :scenario WHERE id = :id"),
            {"scenario": scenario_type, "id": run_id},
        )
        self.session.commit()

    def update_run_status(self, run_id: str, status: str) -> None:
        """Update orch_runs.status in place (e.g. running → paused, failed)."""
        self.session.execute(
            text("UPDATE orch_runs SET status = :status WHERE id = :id"),
            {"status": status, "id": run_id},
        )
        self.session.commit()

    def update_run_completed(
        self,
        run_id: str,
        feedback_score: int | None = None,
        feedback_comment: str | None = None,
    ) -> None:
        """Mark a run as completed and record optional end-user feedback (score 1–4)."""
        self.session.execute(
            text(
                "UPDATE orch_runs "
                "SET status = 'completed', "
                "    completed_at = now(), "
                "    feedback_score = :score, "
                "    feedback_comment = :comment "
                "WHERE id = :id"
            ),
            {"score": feedback_score, "comment": feedback_comment, "id": run_id},
        )
        self.session.commit()

    def update_run_pr(self, run_id: str, pr_url: str, feature_branch: str) -> None:
        """Record the GitHub PR URL and feature branch name on the run row."""
        self.session.execute(
            text(
                "UPDATE orch_runs "
                "SET pr_url = :pr_url, feature_branch = :branch "
                "WHERE id = :id"
            ),
            {"pr_url": pr_url, "branch": feature_branch, "id": run_id},
        )
        self.session.commit()

    def get_run(self, run_id: str) -> dict:
        """Return all columns of an orch_runs row as a plain dict.

        Raises:
            KeyError: if no row with this run_id exists.
        """
        row = self.session.execute(
            text("SELECT * FROM orch_runs WHERE id = :id"),
            {"id": run_id},
        ).mappings().one_or_none()
        if row is None:
            raise KeyError(f"run_id '{run_id}' not found in orch_runs")
        return dict(row)

    def get_pending_runs(self) -> list[dict]:
        """Return all runs currently awaiting gate approval, oldest first."""
        rows = self.session.execute(
            text(
                "SELECT id, requirement, scenario_type, triggered_by, status, created_at "
                "FROM orch_runs "
                "WHERE status LIKE 'awaiting:%' "
                "ORDER BY created_at ASC"
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    # ── orch_stage_results ─────────────────────────────────────────────────

    def save_stage_result(self, result: StageResult, run_id: str) -> None:
        """Insert one orch_stage_results row.

        A stage that is retried produces multiple rows — one per attempt.
        The most recent attempt is always retrievable via get_stage_result().
        """
        self.session.execute(
            text(
                "INSERT INTO orch_stage_results "
                "(run_id, stage_name, status, attempt_number, prompt_version, "
                " model_used, started_at, completed_at, output_artifact, error_message) "
                "VALUES "
                "(:run_id, :stage, :status, :attempt, :prompt_ver, "
                " :model, :started, :completed, cast(:artifact as jsonb), :error)"
            ),
            {
                "run_id": run_id,
                "stage": result.stage_name,
                "status": result.status.value,
                "attempt": result.attempt_number,
                "prompt_ver": result.prompt_version,
                "model": result.model_used,
                "started": result.started_at,
                "completed": result.completed_at,
                "artifact": (
                    json.dumps(result.output_artifact)
                    if result.output_artifact is not None
                    else None
                ),
                "error": result.error_message,
            },
        )
        self.session.commit()

    def get_stage_result(self, run_id: str, stage_name: str) -> dict | None:
        """Return the most recent attempt for a stage, or None if it has not run yet."""
        row = self.session.execute(
            text(
                "SELECT * FROM orch_stage_results "
                "WHERE run_id = :run_id AND stage_name = :stage "
                "ORDER BY attempt_number DESC "
                "LIMIT 1"
            ),
            {"run_id": run_id, "stage": stage_name},
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    def get_all_stage_results(self, run_id: str) -> list[dict]:
        """Return the latest attempt for every stage in a run, ordered by started_at.

        Uses PostgreSQL DISTINCT ON to return one row per stage_name (the one
        with the highest attempt_number), then re-orders by started_at for display.
        """
        rows = self.session.execute(
            text(
                "SELECT DISTINCT ON (stage_name) * "
                "FROM orch_stage_results "
                "WHERE run_id = :run_id "
                "ORDER BY stage_name, attempt_number DESC"
            ),
            {"run_id": run_id},
        ).mappings().all()
        # Re-sort by started_at so the caller sees chronological order
        results = [dict(r) for r in rows]
        results.sort(key=lambda r: r["started_at"] or "")
        return results

    # ── OrchestratorState reconstruction ──────────────────────────────────

    def load_run_state(self, run_id: str) -> OrchestratorState:
        """Reconstruct an OrchestratorState TypedDict from orch_runs + orch_stage_results.

        This is the BUSINESS view of run state — useful for:
          - CLI `approve` command: who triggered the run? what stage is pending?
          - MetricsTracker: what requirement was this run for?
          - Display / summary: what artifacts did each stage produce?

        It is NOT the LangGraph resume state.  For graph resumption, LangGraph's
        PostgresSaver restores the full serialised checkpoint automatically when
        graph.invoke(None, config) is called with the same thread_id.

        Note: stage_evaluations and tool_cache are not stored in orch_ tables.
        stage_evaluations are stored in the LangGraph checkpoint (PostgresSaver).
        tool_cache is in-memory only and is lost across process restarts by design.
        """
        run = self.get_run(run_id)
        stage_rows = self.get_all_stage_results(run_id)

        stage_artifacts: dict[str, Any] = {
            r["stage_name"]: r["output_artifact"]
            for r in stage_rows
            if r.get("output_artifact") is not None
        }

        return OrchestratorState(
            run_id=run["id"],
            requirement=run["requirement"],
            resolved_requirement=run.get("resolved_req") or "",
            scenario_type=run["scenario_type"],
            triggered_by=run["triggered_by"],
            stage_artifacts=stage_artifacts,
            stage_evaluations={},
            feature_branch=run.get("feature_branch"),
            pr_url=run.get("pr_url"),
            schema_change_detected=False,
            assumptions=[],
        )


# ── module-level convenience ─────────────────────────────────────────────────


@contextmanager
def make_store() -> Generator[RunStateStore, None, None]:
    """Create a RunStateStore backed by a fresh SessionLocal session.

    Commits on clean exit, rolls back on exception, always closes the session.

    Usage:
        with make_store() as store:
            store.create_run("orch-001", "Add QR endpoint", "greenfield", "alice")
    """
    session = SessionLocal()
    try:
        yield RunStateStore(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
