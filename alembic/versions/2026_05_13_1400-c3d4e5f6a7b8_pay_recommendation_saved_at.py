"""Add ``saved_at`` to pay_recommendations.

The MoP / MoM screen's "1 of N Completed" counter advances when the
actor clicks "Save & Next" on a card. That click is otherwise a
no-op — the actor is signalling "I've reviewed this card", not
necessarily "I've changed its values". We need a separate timestamp
column distinct from ``updated_at`` (which fires on every cell change)
and from the status (which only flips on submit).

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-13 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pay_recommendations",
        sa.Column(
            "saved_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("pay_recommendations", "saved_at")
