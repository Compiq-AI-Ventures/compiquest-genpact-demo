"""Rename the EMPLOYEE role to IC.

The role added in d1e2f3a4b5c6 was seeded as ``EMPLOYEE``, but the
frontend's routing/dashboard-selection logic was already built against
a role code of ``IC`` (matching this codebase's own "individual
contributor" terminology used throughout the specs/docs). Renaming in
place — rather than deleting + re-inserting — preserves existing
``user_roles`` foreign keys for any tenant that already ran the prior
migration and reseeded.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2f3a4b5c6d7"
down_revision: str | Sequence[str] | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE roles
        SET code = 'IC',
            name = 'Individual Contributor',
            description = 'Individual contributor; no direct reports.',
            updated_at = now()
        WHERE code = 'EMPLOYEE'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE roles
        SET code = 'EMPLOYEE',
            name = 'Employee',
            description = 'Individual contributor.',
            updated_at = now()
        WHERE code = 'IC'
        """
    )
