"""orch tables

Revision ID: 3a8f1c2d4e5b
Revises: 0f952025db76
Create Date: 2026-08-03 14:00:00.000000

Creates the 7 orchestrator tables (orch_ prefix) that co-locate with the
URL shortener service tables in the same PostgreSQL instance.

Table creation order respects foreign key dependencies:
  1. orch_users      — standalone RBAC registry
  2. orch_runs       — standalone, one row per orchestration run
  3. orch_stage_results — FK → orch_runs
  4. orch_audit_events  — FK → orch_runs (append-only — never UPDATE/DELETE)
  5. orch_metrics       — FK → orch_runs
  6. orch_memory        — FK → orch_runs (nullable — seeds have no run)
  7. orch_cache      — standalone response cache

Design notes
------------
- JSONB columns (output_artifact, input_context, details, response) store
  variable-shaped structured data without requiring schema migrations when
  artifact shapes evolve.

- orch_audit_events is intentionally append-only at the application layer.
  No UPDATE or DELETE path exists in the codebase.  This provides a
  tamper-evident audit trail satisfying SOC2 CC7.2 change-control requirements.

- orch_cache uses a SHA-256 prompt_hash unique index as the lookup key.
  expires_at enables row-level TTL enforcement in application code (24h).

- orch_memory.source_run_id is nullable to support seeded facts that exist
  before any run has executed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = '3a8f1c2d4e5b'
down_revision: Union[str, None] = '0f952025db76'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. orch_users — RBAC user registry                                  #
    #    Seeded from config/users.yaml.  github_login is the primary key  #
    #    because it is the stable identity used across all audit events.  #
    # ------------------------------------------------------------------ #
    op.create_table(
        'orch_users',
        sa.Column('github_login', sa.String(100), primary_key=True),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('role', sa.String(30), nullable=False),   # DEVELOPER|TECH_LEAD|RELEASE_MANAGER|ADMIN
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )

    # ------------------------------------------------------------------ #
    # 2. orch_runs — One row per orchestration run                        #
    #    status lifecycle: running → completed | failed | paused          #
    #    feedback_score (1-4) and feedback_comment are set at run end.    #
    # ------------------------------------------------------------------ #
    op.create_table(
        'orch_runs',
        sa.Column('id', sa.String(50), primary_key=True),          # e.g. orch-green-001
        sa.Column('requirement', sa.Text(), nullable=False),
        sa.Column('resolved_req', sa.Text(), nullable=True),        # set after clarification loop
        sa.Column('scenario_type', sa.String(20), nullable=False),  # greenfield|brownfield|ambiguous
        sa.Column('status', sa.String(20), nullable=False, server_default=sa.text("'running'")),
        sa.Column('triggered_by', sa.String(100), nullable=False),  # github_login
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('feature_branch', sa.String(200), nullable=True),
        sa.Column('pr_url', sa.String(500), nullable=True),
        sa.Column('feedback_score', sa.SmallInteger(), nullable=True),   # 1-4
        sa.Column('feedback_comment', sa.Text(), nullable=True),
    )

    # ------------------------------------------------------------------ #
    # 3. orch_stage_results — One row per stage execution attempt         #
    #    attempt_number > 1 means the stage was retried.                  #
    #    input_context: RunContext snapshot at stage start (JSONB).       #
    #    output_artifact: structured LLM output for this stage (JSONB).  #
    # ------------------------------------------------------------------ #
    op.create_table(
        'orch_stage_results',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('run_id', sa.String(50), sa.ForeignKey('orch_runs.id'), nullable=False),
        sa.Column('stage_name', sa.String(50), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('attempt_number', sa.SmallInteger(), nullable=False, server_default=sa.text('1')),
        sa.Column('prompt_version', sa.String(30), nullable=True),   # e.g. architecture_v1
        sa.Column('model_used', sa.String(50), nullable=True),        # e.g. gpt-4o
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('input_context', JSONB(), nullable=True),
        sa.Column('output_artifact', JSONB(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_stage_results_run', 'orch_stage_results', ['run_id'])

    # ------------------------------------------------------------------ #
    # 4. orch_audit_events — Append-only SDLC event log                  #
    #    NEVER updated or deleted — only INSERTed into.                   #
    #    details JSONB absorbs stage-specific payload (approval comments, #
    #    AI evaluation scores, override justifications, etc.).            #
    # ------------------------------------------------------------------ #
    op.create_table(
        'orch_audit_events',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('run_id', sa.String(50), sa.ForeignKey('orch_runs.id'), nullable=False),
        sa.Column('event_type', sa.String(50), nullable=False),
        sa.Column('stage_name', sa.String(50), nullable=True),
        sa.Column('actor', sa.String(100), nullable=False),           # github_login or "system"
        sa.Column('actor_role', sa.String(30), nullable=True),
        sa.Column('details', JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_audit_run', 'orch_audit_events', ['run_id'])
    op.create_index('idx_audit_type', 'orch_audit_events', ['event_type'])

    # ------------------------------------------------------------------ #
    # 5. orch_metrics — Per LLM/tool call observability                  #
    #    Written incrementally by CostTracker after each gateway call.   #
    #    Aggregated at run end by MetricsTracker.summarize().             #
    # ------------------------------------------------------------------ #
    op.create_table(
        'orch_metrics',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('run_id', sa.String(50), sa.ForeignKey('orch_runs.id'), nullable=False),
        sa.Column('stage_name', sa.String(50), nullable=False),
        sa.Column('trace_id', sa.String(100), nullable=True),
        sa.Column('tokens_in', sa.Integer(), nullable=True),
        sa.Column('tokens_out', sa.Integer(), nullable=True),
        sa.Column('cost_usd', sa.Numeric(10, 6), nullable=True),
        sa.Column('llm_latency_ms', sa.Integer(), nullable=True),
        sa.Column('tool_latency_ms', sa.Integer(), nullable=True),
        sa.Column('cache_hit', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('model_used', sa.String(50), nullable=True),
        sa.Column('prompt_version', sa.String(30), nullable=True),
        sa.Column('attempt_count', sa.SmallInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_metrics_run', 'orch_metrics', ['run_id'])

    # ------------------------------------------------------------------ #
    # 6. orch_memory — Cross-run persistent memory                       #
    #    source_run_id is nullable: seeds have no associated run.         #
    #    is_active=False means the memory was superseded or invalidated.  #
    #    memory_type: fact | preference | decision | convention           #
    # ------------------------------------------------------------------ #
    op.create_table(
        'orch_memory',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('source_run_id', sa.String(50), sa.ForeignKey('orch_runs.id'), nullable=True),
        sa.Column('memory_type', sa.String(30), nullable=False),      # fact|preference|decision|convention
        sa.Column('actor', sa.String(100), nullable=False),            # github_login or "seed"
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
    )

    # ------------------------------------------------------------------ #
    # 7. orch_cache — LLM response cache                                 #
    #    Key: SHA-256(full_prompt_text + model_name).                    #
    #    expires_at enforced in app code (24h TTL).                      #
    #    hit_count tracks reuse — useful for cost attribution reporting. #
    # ------------------------------------------------------------------ #
    op.create_table(
        'orch_cache',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('prompt_hash', sa.String(64), nullable=False, unique=True),  # SHA-256 hex
        sa.Column('model_used', sa.String(50), nullable=False),
        sa.Column('response', JSONB(), nullable=False),
        sa.Column('hit_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_cache_hash', 'orch_cache', ['prompt_hash'], unique=True)


def downgrade() -> None:
    # Drop in reverse dependency order to respect foreign key constraints.
    op.drop_table('orch_cache')
    op.drop_table('orch_memory')
    op.drop_table('orch_metrics')
    op.drop_table('orch_audit_events')
    op.drop_table('orch_stage_results')
    op.drop_table('orch_runs')
    op.drop_table('orch_users')
