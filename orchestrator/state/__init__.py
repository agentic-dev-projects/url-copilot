"""
orchestrator.state — PostgreSQL persistence for all orch_ tables.

Files
-----
store.py    RunStateStore — all reads and writes to the 6 orch_ tables.

This package uses the same SessionLocal from service.db.session as the
URL shortener service.  There is intentionally no second database connection
— the orchestrator co-locates its tables in the same PostgreSQL instance,
which simplifies operations (one DB to back up, one connection pool to tune).

Tables managed
--------------
orch_runs           One row per orchestration run (status, triggered_by, PR url)
orch_stage_results  One row per stage execution attempt (artifact, model, version)
orch_audit_events   Append-only event log (see orchestrator.governance)
orch_metrics        One row per LLM/tool call (tokens, cost, latency, cache_hit)
orch_memory         Cross-run memory entries (facts, preferences, decisions)
orch_cache          Response cache (prompt_hash → cached LLM response)

Note: no orch_users table. User identity is resolved at auth time from
config/users.yaml by TokenAuthenticator and passed as a CurrentUser dataclass.
This avoids a dual source of truth problem between the YAML file and a DB table.

Why raw SQL / SQLAlchemy Core instead of new ORM models?
---------------------------------------------------------
The orch_ tables are created via a raw DDL Alembic migration (Phase 2).
Adding SQLAlchemy ORM models for them would require a second declarative base
or careful Base sharing with the service models — added complexity for no gain.
SQLAlchemy Core (text() queries) is sufficient and keeps the orchestrator
tables fully decoupled from the service ORM layer.

Implemented in Phase 4.
"""
