"""create audit_events table

Revision ID: 001_create_audit_events
Revises:
Create Date: 2026-09-28

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001_create_audit_events"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=True),
        sa.Column("prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("prompt_text_redacted", sa.Text(), nullable=True),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "rules_matched",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("policy_version", sa.String(length=16), nullable=True),
        sa.Column("latency_ms", postgresql.REAL(), nullable=True),
        sa.CheckConstraint(
            "verdict IN ('allow', 'block')", name="ck_audit_events_verdict"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_audit_events_tenant_ts",
        "audit_events",
        ["tenant_id", sa.text("ts DESC")],
    )
    op.create_index(
        "ix_audit_events_prompt_hash", "audit_events", ["prompt_hash"]
    )
    op.create_index("ix_audit_events_verdict", "audit_events", ["verdict"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_verdict", table_name="audit_events")
    op.drop_index("ix_audit_events_prompt_hash", table_name="audit_events")
    op.drop_index("ix_audit_events_tenant_ts", table_name="audit_events")
    op.drop_table("audit_events")
