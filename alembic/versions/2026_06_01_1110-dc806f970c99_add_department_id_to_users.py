"""add_department_id_to_users

Revision ID: dc806f970c99
Revises: 448d87bfe133
Create Date: 2026-06-01 11:10:08.104120+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dc806f970c99"
down_revision: str | Sequence[str] | None = "448d87bfe133"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this migration."""
    op.add_column(
        "users",
        sa.Column(
            "department_id",
            sa.Uuid(),
            sa.ForeignKey("departments.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_users_department_id", "users", ["department_id"])


def downgrade() -> None:
    """Revert this migration."""
    op.drop_index("ix_users_department_id", table_name="users")
    op.drop_column("users", "department_id")
