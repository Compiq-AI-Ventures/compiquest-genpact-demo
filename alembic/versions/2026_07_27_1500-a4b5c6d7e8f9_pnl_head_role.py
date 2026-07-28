"""Seed the PNL_HEAD (P&L Head) tenant-scope role.

Business-unit P&L owners get a real role row so they can be granted
``user_roles`` entries and log in with a formally recognized role.
Follows the same idempotent pattern as the EMPLOYEE/IC role addition
in d1e2f3a4b5c6.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4b5c6d7e8f9"
down_revision: str | Sequence[str] | None = "74dd8f2978ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Idempotent insert: if the row already exists, do nothing.
    op.execute(
        """
        INSERT INTO roles (id, code, name, description, scope,
                           is_system_role, is_active,
                           created_at, updated_at)
        VALUES
            (gen_random_uuid(), 'PNL_HEAD', 'P&L Head',
             'Business-unit P&L owner; accountable for a BU''s cost, '
             'headcount, and compensation outcomes end to end.',
             'TENANT', TRUE, TRUE, now(), now())
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM roles WHERE code = 'PNL_HEAD'")
