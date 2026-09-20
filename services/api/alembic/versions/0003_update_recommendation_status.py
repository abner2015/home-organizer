"""Update ``recommendations.status`` CHECK constraint.

Revision ID: 0003_update_recommendation_status
Revises: 0002_assets
Create Date: 2026-09-18

Replaces the status enum to align with the Storage Recommendation Agent
prompt: ``('pending','accepted','rejected','superseded')``. The legacy
``adjusted`` value is no longer accepted; any pre-existing rows are mapped
to ``accepted`` to preserve their semantic meaning (a user took action on
the recommendation).
"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0003_update_recommendation_status"
down_revision = "0002_assets"
branch_labels = None
depends_on = None


# Constraint literals — kept as module constants so upgrade/downgrade stay in
# sync and reviewers can grep for them.
_LEGACY_CHECK = "status IN ('pending','accepted','adjusted','rejected')"
_NEW_CHECK = "status IN ('pending','accepted','rejected','superseded')"
_CONSTRAINT_NAME = "ck_recommendations_status"


def upgrade() -> None:
    # Backfill: any historical row in the legacy ``adjusted`` state becomes
    # ``accepted`` (closest semantic — user acted on the recommendation).
    op.execute("UPDATE recommendations SET status='accepted' WHERE status='adjusted'")
    op.drop_constraint(_CONSTRAINT_NAME, "recommendations", type_="check")
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        "recommendations",
        _NEW_CHECK,
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT_NAME, "recommendations", type_="check")
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        "recommendations",
        _LEGACY_CHECK,
    )