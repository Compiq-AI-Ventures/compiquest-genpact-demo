"""Departments table — first tenant-scoped business entity, with RLS.

This migration also establishes the Row-Level Security pattern that
every future business table follows:

* ``tenant_id UUID NOT NULL`` references ``tenants(id)``.
* ``ALTER TABLE … ENABLE ROW LEVEL SECURITY`` + ``FORCE ROW LEVEL
  SECURITY`` (so the table owner is also subject to the policy).
* A single ``tenant_isolation`` policy with both ``USING`` and
  ``WITH CHECK`` clauses. Reads, inserts, updates, deletes all go
  through the same predicate:

      tenant_id::text = current_setting('app.current_tenant', true)
      OR current_setting('app.platform_override', true) = 'true'

  ``current_setting(name, missing_ok=true)`` returns NULL when the
  GUC isn't set; ``NULL = anything`` is NULL, treated as false — so
  with no GUC, all rows are hidden (fail-closed).

Migrations themselves run with ``app.platform_override = 'true'``
(set by ``alembic/env.py``) so DDL on RLS-enabled tables works.

Revision ID: f8c3d4a5b6e7
Revises: e7f8a9b1c2d3
Create Date: 2026-05-06 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f8c3d4a5b6e7"
down_revision: str | Sequence[str] | None = "e7f8a9b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_departments_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id", "code", name="uq_departments_tenant_code"
        ),
    )
    op.create_index(
        "ix_departments_tenant_id", "departments", ["tenant_id"]
    )

    # ---- Row-Level Security ---------------------------------------------
    # Enable + FORCE so even the table owner is subject to the policy.
    # Without FORCE, the user that owns the table bypasses RLS — that's
    # almost always our application user in dev, which would defeat the
    # purpose of RLS entirely.
    op.execute("ALTER TABLE departments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE departments FORCE ROW LEVEL SECURITY")

    # The tenant-isolation policy. Same predicate for read AND write.
    # current_setting(name, true) returns NULL on unset rather than
    # erroring; the comparison then yields NULL, treated as false.
    op.execute(
        """
        CREATE POLICY tenant_isolation ON departments
            FOR ALL
            USING (
                tenant_id::text = current_setting('app.current_tenant', true)
                OR current_setting('app.platform_override', true) = 'true'
            )
            WITH CHECK (
                tenant_id::text = current_setting('app.current_tenant', true)
                OR current_setting('app.platform_override', true) = 'true'
            )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON departments")
    op.drop_index("ix_departments_tenant_id", table_name="departments")
    op.drop_table("departments")
