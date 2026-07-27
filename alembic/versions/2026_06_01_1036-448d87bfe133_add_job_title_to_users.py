"""add_job_title_to_users

Revision ID: 448d87bfe133
Revises: c3d4e5f6a7b8
Create Date: 2026-06-01 10:36:59.629803+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "448d87bfe133"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this migration."""
    op.add_column("users", sa.Column("job_title", sa.String(length=200), nullable=True))


def downgrade() -> None:
    """Revert this migration."""
    op.drop_column("users", "job_title")
