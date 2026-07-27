"""Seed the EMPLOYEE tenant-scope role.

Individual contributors get a real role row so they can be granted
``user_roles`` entries and log in with a formally recognized role,
instead of existing only as unrolled ``users`` rows. Follows the same
pattern as the CHRO/CFO addition in b2c3d4e5f6a7.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: str | Sequence[str] | None = "f8a9b0c1d2e3"
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
            (gen_random_uuid(), 'EMPLOYEE', 'Employee',
             'Individual contributor.',
             'TENANT', TRUE, TRUE, now(), now())
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM roles WHERE code = 'EMPLOYEE'")
