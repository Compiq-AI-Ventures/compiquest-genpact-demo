"""Dynamic roles and user_roles association.

Replaces the single ``users.role`` ENUM column with two real tables
(``roles`` and ``user_roles``), seeds the built-in role set, and
backfills existing users into the new join table before dropping the
column and the Postgres ``user_role`` ENUM type.

Revision ID: c8a3f72e4d11
Revises: 82cd4ec16a9e
Create Date: 2026-05-05 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c8a3f72e4d11"
down_revision: str | Sequence[str] | None = "82cd4ec16a9e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Snapshot of the built-in role set as of this migration. Kept inline
# (rather than imported from ``app.core.roles``) so future changes to
# the application's seed list don't retroactively alter what THIS
# migration installs.
_DEFAULT_ROLES: tuple[tuple[str, str, str], ...] = (
    ("CXO", "C-Suite Executive", "Top-of-house executive role."),
    ("HR", "Human Resources", "Core HR function."),
    ("HRBP", "HR Business Partner", "HR partner aligned to a business unit."),
    (
        "C_AND_B",
        "Compensation & Benefits",
        "Owns pay structure, benefits design, and benchmarking.",
    ),
    (
        "MANAGER_OF_MANAGERS",
        "Manager of Managers",
        "Oversees other managers; second-line leadership.",
    ),
    ("MANAGER", "Manager", "First-line people manager."),
)


def upgrade() -> None:
    """Create roles + user_roles, seed defaults, backfill, drop old column."""

    # 1. roles table -------------------------------------------------------
    op.create_table(
        "roles",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("code", name="uq_roles_code"),
    )
    op.create_index("ix_roles_code", "roles", ["code"], unique=True)

    # 2. user_roles join table --------------------------------------------
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_user_roles_user"
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], ondelete="RESTRICT", name="fk_user_roles_role"
        ),
        sa.PrimaryKeyConstraint("user_id", "role_id", name="pk_user_roles"),
    )

    # 3. Seed built-in roles ----------------------------------------------
    bind = op.get_bind()
    for code, name, description in _DEFAULT_ROLES:
        bind.execute(
            sa.text(
                """
                INSERT INTO roles (code, name, description, is_active)
                VALUES (:code, :name, :description, true)
                """
            ),
            {"code": code, "name": name, "description": description},
        )

    # 4. Backfill: every existing user gets a row in user_roles --------
    #    Cast users.role (ENUM) to text so we can join on roles.code.
    op.execute(
        """
        INSERT INTO user_roles (user_id, role_id)
        SELECT u.id, r.id
        FROM users u
        JOIN roles r ON r.code = u.role::text
        """
    )

    # 5. Drop the now-redundant column ------------------------------------
    op.drop_column("users", "role")

    # 6. Drop the now-orphaned ENUM type ----------------------------------
    op.execute("DROP TYPE user_role")


def downgrade() -> None:
    """Best-effort revert.

    Re-creates the ``user_role`` ENUM and the ``users.role`` column,
    restores ONE role per user (the earliest by ``user_roles.created_at``),
    then drops the new tables. Any user who held multiple roles loses
    all but one — downgrades for data migrations are inherently lossy.
    """

    # 1. Recreate the user_role ENUM type ---------------------------------
    op.execute(
        """
        CREATE TYPE user_role AS ENUM (
            'CXO', 'HR', 'HRBP', 'C_AND_B',
            'MANAGER_OF_MANAGERS', 'MANAGER'
        )
        """
    )

    # 2. Re-add users.role as nullable so the UPDATE below can populate it.
    op.add_column(
        "users",
        sa.Column(
            "role",
            postgresql.ENUM(
                "CXO",
                "HR",
                "HRBP",
                "C_AND_B",
                "MANAGER_OF_MANAGERS",
                "MANAGER",
                name="user_role",
                create_type=False,
            ),
            nullable=True,
        ),
    )

    # 3. Restore from user_roles (one per user, earliest assignment).
    op.execute(
        """
        UPDATE users u
        SET role = (
            SELECT r.code::user_role
            FROM user_roles ur
            JOIN roles r ON r.id = ur.role_id
            WHERE ur.user_id = u.id
            ORDER BY ur.created_at ASC
            LIMIT 1
        )
        """
    )

    # 4. Make non-nullable. If any user had no role assigned at all,
    #    this will fail — at which point the operator can decide what
    #    to do (set a default, delete the row, etc.).
    op.alter_column("users", "role", nullable=False)

    # 5. Drop the new tables ----------------------------------------------
    op.drop_table("user_roles")
    op.drop_index("ix_roles_code", table_name="roles")
    op.drop_table("roles")
