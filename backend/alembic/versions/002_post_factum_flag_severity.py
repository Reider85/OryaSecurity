"""add post_factum_flag verdict and severity column

Revision ID: 002_post_factum_flag_severity
Revises: 001_create_audit_events
Create Date: 2026-10-20

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_post_factum_flag_severity"
down_revision: Union[str, None] = "001_create_audit_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_events",
        sa.Column("severity", sa.String(length=16), nullable=True),
    )
    op.drop_constraint("ck_audit_events_verdict", "audit_events", type_="check")
    op.create_check_constraint(
        "ck_audit_events_verdict",
        "audit_events",
        "verdict IN ('allow', 'block', 'post_factum_flag')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_audit_events_verdict", "audit_events", type_="check")
    op.create_check_constraint(
        "ck_audit_events_verdict",
        "audit_events",
        "verdict IN ('allow', 'block')",
    )
    op.drop_column("audit_events", "severity")
