"""Widen orch_runs.status and add reviewed_by

Revision ID: c3d2e1f0a9b8
Revises: 3a8f1c2d4e5b
Create Date: 2026-08-03 15:00:00.000000

Changes
-------
1. Widen orch_runs.status from VARCHAR(20) to VARCHAR(50).
   The async approval workflow encodes the pending gate name in the status
   field as "awaiting:{gate_name}" (e.g. "awaiting:architecture_gate" = 26
   chars), which exceeds the original VARCHAR(20) limit.

2. Add orch_runs.reviewed_by VARCHAR(100) nullable.
   Stores the github_login of the approver who approved or rejected the final
   gate, closing the audit loop without duplicating the full orch_audit_events
   trail.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d2e1f0a9b8'
down_revision: Union[str, None] = '3a8f1c2d4e5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'orch_runs', 'status',
        type_=sa.String(50),
        existing_type=sa.String(20),
        existing_nullable=False,
    )
    op.add_column(
        'orch_runs',
        sa.Column('reviewed_by', sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('orch_runs', 'reviewed_by')
    op.alter_column(
        'orch_runs', 'status',
        type_=sa.String(20),
        existing_type=sa.String(50),
        existing_nullable=False,
    )
