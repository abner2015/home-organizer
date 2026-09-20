"""Add `assets` table for Phase 3 image uploads.

Revision ID: 0002_assets
Revises: 0001_initial_schema
Create Date: 2026-09-18

Adds the `assets` table: pre-item uploads stored in MinIO/S3. Each row tracks
the bucket + server-generated object key, validated content-type, size, and
optional SHA-256 hash for dedup. Status starts as 'pending' and is flipped to
'ready' once the bytes have been uploaded and verified.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0002_assets"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", postgresql.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("home_id", postgresql.UUID(), sa.ForeignKey("homes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", postgresql.UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("bucket", sa.String(length=100), nullable=False),
        sa.Column("object_key", sa.String(length=500), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("original_filename", sa.String(length=500), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('pending','ready','failed')", name="ck_assets_status"
        ),
        sa.CheckConstraint("size_bytes >= 0", name="ck_assets_size_non_negative"),
        sa.UniqueConstraint("object_key", name="uq_assets_object_key"),
    )
    op.create_index("ix_assets_home_id_created", "assets", ["home_id", sa.text("created_at DESC")])
    op.create_index("ix_assets_sha256", "assets", ["sha256"])


def downgrade() -> None:
    op.drop_index("ix_assets_sha256", table_name="assets")
    op.drop_index("ix_assets_home_id_created", table_name="assets")
    op.drop_table("assets")
