"""Initial schema — 16 tables per docs/DATABASE.md.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-18

Creates the full home-organizer schema: extensions, tables, indexes (incl.
partial uniques), CHECK constraints, deferrable FK. Every table has a UUID
PK with `gen_random_uuid()` default (pgcrypto) and timestamp columns where
applicable.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------- extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ---------------------------------------------------------------- enum types
    # Use Python-side CHECK constraints (not PG ENUM types) for portability
    # and so we can evolve values without ALTER TYPE dance.
    # CHECK constraints are attached to each table below.

    # ---------------------------------------------------------------- users
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ---------------------------------------------------------------- homes
    op.create_table(
        "homes",
        sa.Column("id", postgresql.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("owner_id", postgresql.UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default=sa.text("'Asia/Shanghai'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_homes_owner_id", "homes", ["owner_id"])

    # ---------------------------------------------------------------- home_memberships
    op.create_table(
        "home_memberships",
        sa.Column("id", postgresql.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("home_id", postgresql.UUID(), sa.ForeignKey("homes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("role IN ('owner','member')", name="ck_home_memberships_role"),
    )
    op.create_index("ix_home_memberships_home_id", "home_memberships", ["home_id"])
    op.create_index(
        "uq_home_memberships_user_home", "home_memberships", ["user_id", "home_id"], unique=True
    )

    # ---------------------------------------------------------------- rooms
    op.create_table(
        "rooms",
        sa.Column("id", postgresql.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("home_id", postgresql.UUID(), sa.ForeignKey("homes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("room_type", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "room_type IN ('bedroom','kitchen','bathroom','study','living','storage','other')",
            name="ck_rooms_room_type",
        ),
    )
    op.create_index("ix_rooms_home_id_sort", "rooms", ["home_id", "sort_order"])

    # ---------------------------------------------------------------- storage_units
    op.create_table(
        "storage_units",
        sa.Column("id", postgresql.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("room_id", postgresql.UUID(), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("unit_type", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "unit_type IN ('cabinet','shelf','drawer_cabinet','box','other')",
            name="ck_storage_units_unit_type",
        ),
    )
    op.create_index("ix_storage_units_room_id_sort", "storage_units", ["room_id", "sort_order"])

    # ---------------------------------------------------------------- storage_sections
    op.create_table(
        "storage_sections",
        sa.Column("id", postgresql.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("unit_id", postgresql.UUID(), sa.ForeignKey("storage_units.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("section_type", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "section_type IN ('layer','drawer','box','compartment','other')",
            name="ck_storage_sections_section_type",
        ),
    )
    op.create_index("ix_storage_sections_unit_id_sort", "storage_sections", ["unit_id", "sort_order"])

    # ---------------------------------------------------------------- storage_slots
    op.create_table(
        "storage_slots",
        sa.Column("id", postgresql.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("section_id", postgresql.UUID(), sa.ForeignKey("storage_sections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("capacity_hint", sa.Text(), nullable=True),
        sa.Column(
            "allowed_categories",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("uq_storage_slots_section_code", "storage_slots", ["section_id", "code"], unique=True)
    op.create_index("ix_storage_slots_section_id_sort", "storage_slots", ["section_id", "sort_order"])

    # ---------------------------------------------------------------- items
    op.create_table(
        "items",
        sa.Column("id", postgresql.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("home_id", postgresql.UUID(), sa.ForeignKey("homes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("subcategory", sa.String(length=100), nullable=True),
        sa.Column("brand", sa.String(length=100), nullable=True),
        sa.Column("estimated_size", sa.String(length=100), nullable=True),
        sa.Column("is_sensitive", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("needs_lock", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("primary_image_id", postgresql.UUID(), nullable=True),
        sa.Column("created_by", postgresql.UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_items_home_id", "items", ["home_id"])
    op.create_index("ix_items_home_category", "items", ["home_id", "category"])

    # ---------------------------------------------------------------- item_images
    op.create_table(
        "item_images",
        sa.Column("id", postgresql.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("item_id", postgresql.UUID(), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "(width IS NULL OR width > 0) AND (height IS NULL OR height > 0)",
            name="ck_item_images_dimensions_positive",
        ),
    )
    op.create_index("ix_item_images_item_id", "item_images", ["item_id"])
    # Partial unique: one primary image per item.
    op.execute(
        "CREATE UNIQUE INDEX uq_item_images_one_primary "
        "ON item_images (item_id) WHERE is_primary = true"
    )

    # Now that item_images exists, attach the deferrable FK from items.
    op.execute(
        "ALTER TABLE items ADD CONSTRAINT fk_items_primary_image "
        "FOREIGN KEY (primary_image_id) REFERENCES item_images(id) "
        "ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED"
    )

    # ---------------------------------------------------------------- item_placements
    op.create_table(
        "item_placements",
        sa.Column("id", postgresql.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("item_id", postgresql.UUID(), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slot_id", postgresql.UUID(), sa.ForeignKey("storage_slots.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("placed_by", postgresql.UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "recommendation_id",
            postgresql.UUID(),
            sa.ForeignKey("recommendations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "source IN ('ai_recommendation','user_manual')",
            name="ck_item_placements_source",
        ),
    )
    op.create_index("ix_item_placements_item_id", "item_placements", ["item_id"])
    op.create_index("ix_item_placements_slot_id", "item_placements", ["slot_id"])
    op.execute(
        "CREATE INDEX ix_item_placements_slot_active "
        "ON item_placements (slot_id) WHERE removed_at IS NULL"
    )
    # Partial unique: one active placement per item.
    op.execute(
        "CREATE UNIQUE INDEX uq_item_placements_one_active_per_item "
        "ON item_placements (item_id) WHERE removed_at IS NULL"
    )

    # ---------------------------------------------------------------- home_rules
    op.create_table(
        "home_rules",
        sa.Column("id", postgresql.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("home_id", postgresql.UUID(), sa.ForeignKey("homes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("rule_type", sa.Text(), nullable=False),
        sa.Column("scope", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("rule_type IN ('hard','soft')", name="ck_home_rules_rule_type"),
    )
    op.create_index("ix_home_rules_home_id_enabled", "home_rules", ["home_id", "enabled"])

    # ---------------------------------------------------------------- user_preferences
    op.create_table(
        "user_preferences",
        sa.Column("id", postgresql.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("home_id", postgresql.UUID(), sa.ForeignKey("homes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "uq_user_preferences_user_home_key",
        "user_preferences",
        ["user_id", "home_id", "key"],
        unique=True,
    )

    # ---------------------------------------------------------------- conversations
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("home_id", postgresql.UUID(), sa.ForeignKey("homes.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "item_id",
            postgresql.UUID(),
            sa.ForeignKey("items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])

    # ---------------------------------------------------------------- messages
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "conversation_id",
            postgresql.UUID(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tool_calls", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("role IN ('user','assistant','tool')", name="ck_messages_role"),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id", "created_at"])

    # ---------------------------------------------------------------- agent_traces
    op.create_table(
        "agent_traces",
        sa.Column("id", postgresql.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "item_id",
            postgresql.UUID(),
            sa.ForeignKey("items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("user_id", postgresql.UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("home_id", postgresql.UUID(), sa.ForeignKey("homes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("steps", postgresql.JSONB(), nullable=False),
        sa.Column("final_status", sa.Text(), nullable=False),
        sa.Column("total_duration_ms", sa.Integer(), nullable=False),
        sa.Column("llm_tokens_in", sa.Integer(), nullable=True),
        sa.Column("llm_tokens_out", sa.Integer(), nullable=True),
        sa.Column("llm_cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "final_status IN ('success','verifier_failed','error')",
            name="ck_agent_traces_final_status",
        ),
    )
    op.create_index("ix_agent_traces_item_id", "agent_traces", ["item_id"])
    op.create_index("ix_agent_traces_home_id_created", "agent_traces", ["home_id", sa.text("created_at DESC")])

    # ---------------------------------------------------------------- recommendations
    # Created LAST because item_placements.recommendation_id references it.
    op.create_table(
        "recommendations",
        sa.Column("id", postgresql.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("item_id", postgresql.UUID(), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "agent_trace_id",
            postgresql.UUID(),
            sa.ForeignKey("agent_traces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("candidates", postgresql.JSONB(), nullable=False),
        sa.Column("pre_filter_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("post_filter_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "chosen_slot_id",
            postgresql.UUID(),
            sa.ForeignKey("storage_slots.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('pending','accepted','adjusted','rejected')",
            name="ck_recommendations_status",
        ),
    )
    op.create_index("ix_recommendations_item_id", "recommendations", ["item_id"])
    op.create_index("ix_recommendations_status", "recommendations", ["status"])


def downgrade() -> None:
    # Drop in reverse dependency order.
    op.drop_table("recommendations")
    op.drop_table("agent_traces")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("user_preferences")
    op.drop_table("home_rules")
    # Drop items -> primary_image FK first so item_images can be dropped.
    op.execute("ALTER TABLE items DROP CONSTRAINT IF EXISTS fk_items_primary_image")
    op.drop_table("item_placements")
    op.drop_table("item_images")
    op.drop_table("items")
    op.drop_table("storage_slots")
    op.drop_table("storage_sections")
    op.drop_table("storage_units")
    op.drop_table("rooms")
    op.drop_table("home_memberships")
    op.drop_table("homes")
    op.drop_table("users")
    # Leave pgcrypto extension in place — other apps may rely on it.
