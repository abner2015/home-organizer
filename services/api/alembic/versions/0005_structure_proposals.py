"""Add ``structure_proposals`` table (P1.3).

Revision ID: 0005_structure_proposals
Revises: 0004_recommendation_revoked
Create Date: 2026-09-28

Before P1.3 ``POST /api/v1/structures/propose`` returned the proposal but
**never persisted it**. This migration adds the table that lets the user
review drafts later and lets ``accept_proposal`` materialise the tree
atomically (one transaction).

**Pending proposals are inert**: they do not appear in
``GET /homes/{id}/space-tree`` and do not feed the recommendation pipeline.
The original ``AGENTS.md`` §3.3 rule (AI cannot silently create structure)
is preserved — only the ``accepted`` state produces real rooms / units /
sections / slots rows.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0005_structure_proposals"
down_revision = "0004_recommendation_revoked"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "structure_proposals",
        sa.Column(
            "id",
            postgresql.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "home_id",
            postgresql.UUID(),
            sa.ForeignKey("homes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            comment="The user who generated this proposal.",
        ),
        sa.Column(
            "source",
            sa.Text(),
            nullable=False,
            comment="photo | text | template",
        ),
        sa.Column(
            "asset_id",
            postgresql.UUID(),
            sa.ForeignKey("assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=True,
            comment="Free-text description the user gave the model.",
        ),
        sa.Column(
            "proposal",
            postgresql.JSONB(),
            nullable=False,
            comment="Recursive 4-level tree: rooms[*].units[*].sections[*].slots[*]",
        ),
        sa.Column(
            "warnings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="Step-4 rewrites the model made (truncation, dedup, etc.)",
        ),
        sa.Column(
            "trace_id",
            postgresql.UUID(),
            sa.ForeignKey("agent_traces.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("rejection_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','accepted','rejected','superseded')",
            name="ck_structure_proposals_status",
        ),
    )
    op.create_index(
        "ix_structure_proposals_home_id",
        "structure_proposals",
        ["home_id"],
    )
    op.create_index(
        "ix_structure_proposals_status",
        "structure_proposals",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_structure_proposals_status",
        table_name="structure_proposals",
    )
    op.drop_index(
        "ix_structure_proposals_home_id",
        table_name="structure_proposals",
    )
    op.drop_table("structure_proposals")
