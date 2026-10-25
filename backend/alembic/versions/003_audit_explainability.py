"""add explainability JSONB column to audit_events

Revision ID: 003_audit_explainability
Revises: 002_post_factum_flag_severity
Create Date: 2026-10-26

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003_audit_explainability"
down_revision: Union[str, None] = "002_post_factum_flag_severity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_events",
        sa.Column(
            "explainability",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("audit_events", "explainability")
