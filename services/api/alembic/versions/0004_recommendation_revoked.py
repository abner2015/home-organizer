"""Add ``revoked`` to ``recommendations.status`` CHECK constraint.

Revision ID: 0004_recommendation_revoked
Revises: 0003_update_recommendation_status
Create Date: 2026-09-22

``revoked`` is the symmetric of ``rejected``: it records that a user reversed
a previous rejection, so the slot becomes recommendable again. The exclusion
set in :func:`app.tools.recommendation_tools.get_rejected_slot_ids` filters by
``status='rejected'`` — flipping to ``revoked`` removes the row from the set
with zero code changes on the read path.

No data migration is needed (no existing row can be in ``revoked`` before this
migration runs).
"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0004_recommendation_revoked"
down_revision = "0003_update_recommendation_status"
branch_labels = None
depends_on = None


# Constraint literals — kept as module constants so upgrade/downgrade stay in
# sync and reviewers can grep for them.
_PREVIOUS_CHECK = (
    "status IN ('pending','accepted','rejected','superseded')"
)
_NEW_CHECK = (
    "status IN ('pending','accepted','rejected','superseded','revoked')"
)
_CONSTRAINT_NAME = "ck_recommendations_status"


def upgrade() -> None:
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
        _PREVIOUS_CHECK,
    )
