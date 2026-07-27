"""Single-tenant-per-user refactor.

Background
----------
The original multi-tenant model let one user belong to many tenants
(via a ``tenant_users`` join table) and carried a ``tenant_id`` on
``user_roles`` so each grant could pin to a specific tenant. That
flexibility turned out to be unnecessary for our SaaS model, where a
single human always operates inside exactly one customer.

This migration collapses the model:

* ``users.tenant_id`` (nullable). NULL = platform user. NOT NULL =
  tenant user. The old ``tenant_users`` table is dropped.
* ``users`` email uniqueness becomes per-tenant via
  ``UNIQUE (tenant_id, email) NULLS NOT DISTINCT`` (PG 15+). Two
  tenants can each have ``alice@hr.com``; platform users (tenant_id
  NULL) are still globally unique because NULLS NOT DISTINCT treats
  two NULLs as equal.
* ``user_roles.tenant_id`` is dropped. The tenant a grant applies in
  is implied by ``users.tenant_id``. Composite uniqueness reduces to
  ``UNIQUE (user_id, role_id)``.
* ``tenants.domain`` becomes NOT NULL + UNIQUE — the canonical
  email/SSO discovery anchor.

This migration assumes v0.1 has no production data; it drops + rebuilds
the relevant constraints rather than carrying old rows forward. If you
ever need to re-apply this against a populated database, write a data
migration that backfills users.tenant_id from tenant_users (picking the
single membership) and rejects users with multiple memberships.

Revision ID: a1b2c3d4e5f6
Revises: f8c3d4a5b6e7
Create Date: 2026-05-12 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f8c3d4a5b6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------------------------------------------------------------
    # 1. Drop the old multi-tenant join + role-grant constraints.
    # ---------------------------------------------------------------------
    # tenant_users goes away entirely — single tenant per user means the
    # table carries no information that isn't already on users.tenant_id.
    op.drop_table("tenant_users")

    # Old composite unique on user_roles included tenant_id. Drop it; the
    # replacement two-column unique is added below.
    op.drop_constraint(
        "uq_user_roles_user_role_tenant",
        "user_roles",
        type_="unique",
    )
    op.drop_index("ix_user_roles_tenant_id", table_name="user_roles")
    op.drop_constraint(
        "fk_user_roles_tenant", "user_roles", type_="foreignkey"
    )
    op.drop_column("user_roles", "tenant_id")
    op.create_unique_constraint(
        "uq_user_roles_user_role",
        "user_roles",
        ["user_id", "role_id"],
    )

    # ---------------------------------------------------------------------
    # 2. users.tenant_id + per-tenant email uniqueness.
    # ---------------------------------------------------------------------
    # Old email uniqueness was enforced as a UNIQUE INDEX named
    # ``ix_users_email`` (created by the very first migration via
    # ``op.create_index(..., unique=True)``), NOT as a named UNIQUE
    # constraint. Drop the index, then re-create a non-unique index
    # (we still want fast email lookups for login resolution) and add
    # the new composite UNIQUE constraint.
    op.drop_index("ix_users_email", table_name="users")

    op.add_column(
        "users",
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])
    # Plain (non-unique) index on email — login resolution still needs
    # to look users up by email + tenant_id, and the composite unique
    # below covers the (tenant_id, email) lookup but not bare email.
    op.create_index("ix_users_email", "users", ["email"])
    op.create_foreign_key(
        "fk_users_tenant",
        "users",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # NULLS NOT DISTINCT (PG 15+) so platform users (tenant_id NULL)
    # remain globally unique on email.
    op.create_unique_constraint(
        "uq_users_tenant_email",
        "users",
        ["tenant_id", "email"],
        postgresql_nulls_not_distinct=True,
    )

    # ---------------------------------------------------------------------
    # 3. tenants.domain — required + unique.
    # ---------------------------------------------------------------------
    # The schema layer regex-validates the domain syntax before we get
    # here; the DB just needs to enforce presence and uniqueness. There
    # is no production data to backfill at v0.1, so we don't need a
    # default; existing rows (if any in dev) will fail the NOT NULL
    # alter unless they already have a domain set.
    op.alter_column("tenants", "domain", nullable=False)
    op.create_unique_constraint(
        "uq_tenants_domain", "tenants", ["domain"]
    )
    op.create_index("ix_tenants_domain", "tenants", ["domain"])


def downgrade() -> None:
    # 3. Tenant domain optional + non-unique again.
    op.drop_index("ix_tenants_domain", table_name="tenants")
    op.drop_constraint("uq_tenants_domain", "tenants", type_="unique")
    op.alter_column("tenants", "domain", nullable=True)

    # 2. Restore single-column unique email index; drop tenant_id from users.
    op.drop_constraint("uq_users_tenant_email", "users", type_="unique")
    op.drop_constraint("fk_users_tenant", "users", type_="foreignkey")
    op.drop_index("ix_users_tenant_id", table_name="users")
    op.drop_column("users", "tenant_id")
    op.drop_index("ix_users_email", table_name="users")
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # 1. Restore user_roles.tenant_id + composite unique, and rebuild
    #    tenant_users.
    op.drop_constraint(
        "uq_user_roles_user_role", "user_roles", type_="unique"
    )
    op.add_column(
        "user_roles",
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_user_roles_tenant_id", "user_roles", ["tenant_id"]
    )
    op.create_foreign_key(
        "fk_user_roles_tenant",
        "user_roles",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_user_roles_user_role_tenant",
        "user_roles",
        ["user_id", "role_id", "tenant_id"],
        postgresql_nulls_not_distinct=True,
    )

    op.create_table(
        "tenant_users",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
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
        sa.UniqueConstraint(
            "tenant_id", "user_id", name="uq_tenant_users_tenant_user"
        ),
    )
