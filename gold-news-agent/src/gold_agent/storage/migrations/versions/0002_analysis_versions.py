"""analysis versions (append-only, for no-lookahead replays)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-14 01:40:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import gold_agent.storage.tables

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_versions",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("cluster_id", sa.String(length=96), nullable=False),
        sa.Column(
            "version_at", gold_agent.storage.tables.UTCDateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("analysis_versions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_analysis_versions_cluster_id"), ["cluster_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_analysis_versions_version_at"), ["version_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("analysis_versions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_analysis_versions_version_at"))
        batch_op.drop_index(batch_op.f("ix_analysis_versions_cluster_id"))
    op.drop_table("analysis_versions")
