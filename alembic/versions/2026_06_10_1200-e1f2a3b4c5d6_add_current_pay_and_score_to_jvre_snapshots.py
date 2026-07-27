"""add_current_pay_and_score_to_jvre_snapshots

Revision ID: e1f2a3b4c5d6
Revises: dc806f970c99
Create Date: 2026-06-10 12:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "dc806f970c99"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this migration."""
    op.add_column(
        "jvre_snapshots",
        sa.Column("current_base", sa.Numeric(18, 2), nullable=True),
    )
    op.add_column(
        "jvre_snapshots",
        sa.Column("current_variable", sa.Numeric(18, 2), nullable=True),
    )
    op.add_column(
        "jvre_snapshots",
        sa.Column("jvre_score", sa.Numeric(5, 2), nullable=True),
    )
    op.add_column(
        "jvre_snapshots",
        sa.Column("current_fy_vesting_units", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Revert this migration."""
    op.drop_column("jvre_snapshots", "current_fy_vesting_units")
    op.drop_column("jvre_snapshots", "jvre_score")
    op.drop_column("jvre_snapshots", "current_variable")
    op.drop_column("jvre_snapshots", "current_base")
