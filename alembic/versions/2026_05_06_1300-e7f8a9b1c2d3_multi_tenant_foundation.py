"""Multi-tenant foundation.

Adds the multi-tenant data model:

* ``tenants`` table (one row per customer org).
* ``tenant_users`` association.
* ``roles.scope`` + ``roles.is_system_role`` (existing rows default to
  ``scope='TENANT'``, ``is_system_role=true``).
* New seed rows: ``SUPER_ADMIN``, ``PLATFORM_ADMIN``, ``SUPPORT_ADMIN``
  (PLATFORM scope) and ``TENANT_ADMIN`` (TENANT scope).
* ``user_roles`` reshape: synthetic ``id`` PK; new nullable
  ``tenant_id`` FK; ``UNIQUE … NULLS NOT DISTINCT`` on
  ``(user_id, role_id, tenant_id)``.
* ``audit_logs.tenant_id`` nullable FK.

Existing user / role-grant / audit data is preserved verbatim: every
existing ``user_roles`` row keeps its ``user_id`` / ``role_id`` and
gets ``tenant_id = NULL`` (platform-level grant from the old single-
tenant world). Operators must run a follow-up data migration (per
customer rollout) to attach those grants to a real tenant.

Requires PostgreSQL 15+ (``UNIQUE NULLS NOT DISTINCT``).

Revision ID: e7f8a9b1c2d3
Revises: d4e5f6a7b8c9
Create Date: 2026-05-06 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e7f8a9b1c2d3"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Snapshot of role rows added by THIS migration. Existing roles
# (CXO / HR / HRBP / C_AND_B / MANAGER_OF_MANAGERS / MANAGER) were
# inserted by an earlier revision and are not re-seeded here.
_NEW_ROLES: tuple[tuple[str, str, str, str], ...] = (
    (
        "SUPER_ADMIN",
        "Super Admin",
        "Unrestricted platform-wide administrator.",
        "PLATFORM",
    ),
    (
        "PLATFORM_ADMIN",
        "Platform Admin",
        "Operates the platform but not customer data.",
        "PLATFORM",
    ),
    (
        "SUPPORT_ADMIN",
        "Support Admin",
        "Customer-support engineer with cross-tenant read access.",
        "PLATFORM",
    ),
    (
        "TENANT_ADMIN",
        "Tenant Admin",
        "Owner / administrator of a single tenant.",
        "TENANT",
    ),
)


def upgrade() -> None:
    """Stand up tenants + tenant_users; reshape roles, user_roles, audit_logs."""

    # 1. tenants ----------------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="ACTIVE",
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
        sa.UniqueConstraint("code", name="uq_tenants_code"),
    )
    op.create_index("ix_tenants_code", "tenants", ["code"], unique=True)

    # 2. tenant_users -----------------------------------------------------
    op.create_table(
        "tenant_users",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_tenant_users_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_tenant_users_user",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "user_id", name="pk_tenant_users"),
    )

    # 3. roles: add scope + is_system_role -------------------------------
    # Existing rows are all the v1 tenant-scoped seeds (HR, CXO, ...) so
    # the TENANT default is correct; if any custom roles were added by
    # hand they'll inherit TENANT and need a manual UPDATE later.
    op.add_column(
        "roles",
        sa.Column(
            "scope",
            sa.String(length=32),
            nullable=False,
            server_default="TENANT",
        ),
    )
    op.add_column(
        "roles",
        sa.Column(
            "is_system_role",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.create_index("ix_roles_scope", "roles", ["scope"])

    # 4. Seed the new role rows ------------------------------------------
    bind = op.get_bind()
    for code, name, description, scope in _NEW_ROLES:
        bind.execute(
            sa.text(
                """
                INSERT INTO roles (code, name, description, scope, is_system_role, is_active)
                VALUES (:code, :name, :description, :scope, true, true)
                """
            ),
            {
                "code": code,
                "name": name,
                "description": description,
                "scope": scope,
            },
        )

    # 5. user_roles reshape -----------------------------------------------
    # Drop the natural composite PK and replace it with a synthetic id so
    # tenant_id can be nullable. The natural-key uniqueness moves to a
    # UNIQUE constraint with NULLS NOT DISTINCT (PG 15+).
    op.drop_constraint("pk_user_roles", "user_roles", type_="primary")

    op.add_column(
        "user_roles",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
    )
    op.create_primary_key("pk_user_roles", "user_roles", ["id"])

    op.add_column(
        "user_roles",
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_user_roles_tenant",
        "user_roles",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_user_roles_tenant_id", "user_roles", ["tenant_id"])
    op.create_unique_constraint(
        "uq_user_roles_user_role_tenant",
        "user_roles",
        ["user_id", "role_id", "tenant_id"],
        postgresql_nulls_not_distinct=True,
    )

    # 6. audit_logs.tenant_id --------------------------------------------
    op.add_column(
        "audit_logs",
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_audit_logs_tenant",
        "audit_logs",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_audit_logs_tenant_id", "audit_logs", ["tenant_id"])


def downgrade() -> None:
    """Reverse the multi-tenant changes.

    Drops the new tables, columns, indexes, and seed rows. Existing
    ``user_roles`` rows keep their ``user_id`` / ``role_id`` and lose
    only the (synthetic) ``id`` and (always-NULL) ``tenant_id``
    columns, so no row data is lost on the way down.
    """

    # 6. audit_logs.tenant_id (reverse) -----------------------------------
    op.drop_index("ix_audit_logs_tenant_id", table_name="audit_logs")
    op.drop_constraint("fk_audit_logs_tenant", "audit_logs", type_="foreignkey")
    op.drop_column("audit_logs", "tenant_id")

    # 5. user_roles reshape (reverse) -------------------------------------
    op.drop_constraint(
        "uq_user_roles_user_role_tenant", "user_roles", type_="unique"
    )
    op.drop_index("ix_user_roles_tenant_id", table_name="user_roles")
    op.drop_constraint("fk_user_roles_tenant", "user_roles", type_="foreignkey")
    op.drop_column("user_roles", "tenant_id")
    op.drop_constraint("pk_user_roles", "user_roles", type_="primary")
    op.drop_column("user_roles", "id")
    op.create_primary_key("pk_user_roles", "user_roles", ["user_id", "role_id"])

    # 4. New role rows (reverse) ------------------------------------------
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            DELETE FROM roles
            WHERE code IN ('SUPER_ADMIN', 'PLATFORM_ADMIN', 'SUPPORT_ADMIN', 'TENANT_ADMIN')
            """
        )
    )

    # 3. roles columns (reverse) ------------------------------------------
    op.drop_index("ix_roles_scope", table_name="roles")
    op.drop_column("roles", "is_system_role")
    op.drop_column("roles", "scope")

    # 2. tenant_users (reverse) -------------------------------------------
    op.drop_table("tenant_users")

    # 1. tenants (reverse) ------------------------------------------------
    op.drop_index("ix_tenants_code", table_name="tenants")
    op.drop_table("tenants")
