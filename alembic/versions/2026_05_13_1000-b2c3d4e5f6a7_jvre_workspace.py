"""JVRE workspace foundation: cycles, budgets, recommendations, JVRE snapshots, references.

Phase 1 of the JVRE workspace per docs/specs/jvre_workspace_v0.1.md. In
one revision so the new domain lands atomically:

* Adds ``default_currency_code`` to ``tenants`` (defaults to USD).
* Seeds two new tenant-scope role rows: CHRO + CFO.
* Creates eleven new tables:
  - ``compensation_cycles``
  - ``reporting_relationships``
  - ``budget_allocations`` (self-referential parent_allocation_id)
  - ``budget_allocation_lines``
  - ``pay_recommendations`` (self-referential parent_recommendation_id)
  - ``pay_recommendation_components``
  - ``pay_recommendation_overrides``
  - ``pay_recommendation_annotations``
  - ``jvre_snapshots``
  - ``market_benchmarks``
  - ``compensation_history``
* Enables + FORCEs RLS on every tenant-scoped table and installs the
  same ``tenant_isolation`` policy used by ``departments`` (USING +
  WITH CHECK against ``app.current_tenant`` GUC, with the
  ``app.platform_override`` escape hatch for cross-tenant admin work).
* Grants SELECT on the read-side tables to the ``rls_tester`` role so
  the existing RLS isolation test pattern (see
  ``tests/test_departments.py``) extends naturally.

Currency strategy
-----------------
Every monetary column carries a paired ``currency_code CHAR(3)`` even
though v0.1 ships single-currency (USD). Multi-currency support
becomes a data fact, not a migration. See spec section 5 for the
formula side; see spec Q5 in the locked decisions for the rationale.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-13 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tenant-scoped tables — RLS is enabled + FORCED on each. The DDL
# pattern matches ``departments`` exactly, so adding a new table to
# this list is all it takes to extend the defense-in-depth surface.
_TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "compensation_cycles",
    "reporting_relationships",
    "budget_allocations",
    "budget_allocation_lines",
    "pay_recommendations",
    "pay_recommendation_components",
    "pay_recommendation_overrides",
    "pay_recommendation_annotations",
    "jvre_snapshots",
    "market_benchmarks",
    "compensation_history",
)

# Tables that don't carry tenant_id directly — they inherit isolation
# through their FK chain. RLS isn't applied at this layer; the parent's
# policy + the cascade FK constraints are the enforcement.
_FK_ISOLATED_TABLES: frozenset[str] = frozenset(
    {
        "budget_allocation_lines",
        "pay_recommendation_components",
        "pay_recommendation_overrides",
        "pay_recommendation_annotations",
    }
)

_NUMERIC_MONEY = sa.Numeric(18, 2)
_NUMERIC_RATIO = sa.Numeric(5, 2)


def _money_column(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, _NUMERIC_MONEY, nullable=nullable)


def _currency_column() -> sa.Column:
    return sa.Column(
        "currency_code",
        sa.String(length=3),
        nullable=False,
        server_default="USD",
    )


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
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
    )


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------
def upgrade() -> None:
    # ---------------- tenants.default_currency_code -------------------
    op.add_column(
        "tenants",
        sa.Column(
            "default_currency_code",
            sa.String(length=3),
            nullable=False,
            server_default="USD",
        ),
    )

    # ---------------- new role rows (CHRO + CFO) ----------------------
    # Idempotent insert: if the rows already exist (re-running on a
    # cluster that's seen this migration's predecessor manually), do
    # nothing.
    op.execute(
        """
        INSERT INTO roles (id, code, name, description, scope,
                           is_system_role, is_active,
                           created_at, updated_at)
        VALUES
            (gen_random_uuid(), 'CHRO', 'Chief Human Resources Officer',
             'Tenant''s senior HR executive; owns the compensation '
             'framework and has read access across the cycle.',
             'TENANT', TRUE, TRUE, now(), now()),
            (gen_random_uuid(), 'CFO', 'Chief Financial Officer',
             'Tenant''s senior finance executive; owns the root budget '
             'allocation that seeds every downstream pool.',
             'TENANT', TRUE, TRUE, now(), now())
        ON CONFLICT (code) DO NOTHING
        """
    )

    # ---------------- compensation_cycles -----------------------------
    op.create_table(
        "compensation_cycles",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("fy_label", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("submission_deadline", sa.Date(), nullable=True),
        _currency_column(),
        sa.Column(
            "jvre_alignment_tolerance",
            sa.Float(),
            nullable=False,
            server_default="0.005",
        ),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_compensation_cycles_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_compensation_cycles_created_by_user",
        ),
        sa.UniqueConstraint(
            "tenant_id", "fy_label", name="uq_compensation_cycles_tenant_fy"
        ),
    )
    op.create_index(
        "ix_compensation_cycles_tenant_id",
        "compensation_cycles",
        ["tenant_id"],
    )

    # ---------------- reporting_relationships -------------------------
    op.create_table(
        "reporting_relationships",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column("manager_user_id", sa.Uuid(), nullable=False),
        sa.Column("report_user_id", sa.Uuid(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
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
            name="fk_reporting_relationships_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"],
            ["compensation_cycles.id"],
            ondelete="CASCADE",
            name="fk_reporting_relationships_cycle",
        ),
        sa.ForeignKeyConstraint(
            ["manager_user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_reporting_relationships_manager",
        ),
        sa.ForeignKeyConstraint(
            ["report_user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_reporting_relationships_report",
        ),
        sa.UniqueConstraint(
            "cycle_id",
            "report_user_id",
            name="uq_reporting_relationships_cycle_report",
        ),
    )
    op.create_index(
        "ix_reporting_relationships_tenant_id",
        "reporting_relationships",
        ["tenant_id"],
    )
    op.create_index(
        "ix_reporting_relationships_cycle_id",
        "reporting_relationships",
        ["cycle_id"],
    )
    op.create_index(
        "ix_reporting_relationships_manager_user_id",
        "reporting_relationships",
        ["manager_user_id"],
    )
    op.create_index(
        "ix_reporting_relationships_report_user_id",
        "reporting_relationships",
        ["report_user_id"],
    )

    # ---------------- budget_allocations ------------------------------
    op.create_table(
        "budget_allocations",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("parent_allocation_id", sa.Uuid(), nullable=True),
        _money_column("total_pool"),
        sa.Column(
            "strategic_reserve",
            _NUMERIC_MONEY,
            nullable=False,
            server_default="0",
        ),
        _money_column("budget_for_allocation"),
        _currency_column(),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column(
            "submitted_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("submitted_by_user_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_budget_allocations_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"],
            ["compensation_cycles.id"],
            ondelete="CASCADE",
            name="fk_budget_allocations_cycle",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_budget_allocations_owner",
        ),
        sa.ForeignKeyConstraint(
            ["parent_allocation_id"],
            ["budget_allocations.id"],
            ondelete="CASCADE",
            name="fk_budget_allocations_parent",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_budget_allocations_submitted_by",
        ),
        sa.UniqueConstraint(
            "cycle_id",
            "owner_user_id",
            name="uq_budget_allocations_cycle_owner",
        ),
    )
    op.create_index(
        "ix_budget_allocations_tenant_id",
        "budget_allocations",
        ["tenant_id"],
    )
    op.create_index(
        "ix_budget_allocations_cycle_id",
        "budget_allocations",
        ["cycle_id"],
    )
    op.create_index(
        "ix_budget_allocations_owner_user_id",
        "budget_allocations",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_budget_allocations_parent_allocation_id",
        "budget_allocations",
        ["parent_allocation_id"],
    )

    # ---------------- budget_allocation_lines -------------------------
    op.create_table(
        "budget_allocation_lines",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("allocation_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_user_id", sa.Uuid(), nullable=False),
        _money_column("allocated_amount"),
        _money_column("base_pool"),
        _money_column("variable_pool"),
        _money_column("lti_grant_fmv_pool"),
        _money_column("reserve_pool"),
        _money_column("jvre_rec_amount"),
        _currency_column(),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["allocation_id"],
            ["budget_allocations.id"],
            ondelete="CASCADE",
            name="fk_budget_allocation_lines_allocation",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_budget_allocation_lines_recipient",
        ),
        sa.UniqueConstraint(
            "allocation_id",
            "recipient_user_id",
            name="uq_budget_allocation_lines_allocation_recipient",
        ),
    )
    op.create_index(
        "ix_budget_allocation_lines_allocation_id",
        "budget_allocation_lines",
        ["allocation_id"],
    )
    op.create_index(
        "ix_budget_allocation_lines_recipient_user_id",
        "budget_allocation_lines",
        ["recipient_user_id"],
    )

    # ---------------- pay_recommendations -----------------------------
    op.create_table(
        "pay_recommendations",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("subject_user_id", sa.Uuid(), nullable=False),
        sa.Column("parent_recommendation_id", sa.Uuid(), nullable=True),
        sa.Column(
            "relationship_kind", sa.String(length=48), nullable=False
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="DRAFT",
        ),
        _currency_column(),
        sa.Column(
            "submitted_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "approved_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_pay_recommendations_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"],
            ["compensation_cycles.id"],
            ondelete="CASCADE",
            name="fk_pay_recommendations_cycle",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_pay_recommendations_actor",
        ),
        sa.ForeignKeyConstraint(
            ["subject_user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_pay_recommendations_subject",
        ),
        sa.ForeignKeyConstraint(
            ["parent_recommendation_id"],
            ["pay_recommendations.id"],
            ondelete="CASCADE",
            name="fk_pay_recommendations_parent",
        ),
        sa.UniqueConstraint(
            "cycle_id",
            "actor_user_id",
            "subject_user_id",
            "relationship_kind",
            name="uq_pay_recommendations_cycle_actor_subject_kind",
        ),
    )
    op.create_index(
        "ix_pay_recommendations_tenant_id",
        "pay_recommendations",
        ["tenant_id"],
    )
    op.create_index(
        "ix_pay_recommendations_cycle_id",
        "pay_recommendations",
        ["cycle_id"],
    )
    op.create_index(
        "ix_pay_recommendations_actor_user_id",
        "pay_recommendations",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_pay_recommendations_subject_user_id",
        "pay_recommendations",
        ["subject_user_id"],
    )
    op.create_index(
        "ix_pay_recommendations_parent_recommendation_id",
        "pay_recommendations",
        ["parent_recommendation_id"],
    )

    # ---------------- pay_recommendation_components -------------------
    op.create_table(
        "pay_recommendation_components",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("recommendation_id", sa.Uuid(), nullable=False),
        sa.Column("component", sa.String(length=32), nullable=False),
        _money_column("current_value", nullable=True),
        _money_column("jvre_rec_value", nullable=True),
        _money_column("mgr_rec_value", nullable=True),
        _money_column("mom_rec_value", nullable=True),
        _currency_column(),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["recommendation_id"],
            ["pay_recommendations.id"],
            ondelete="CASCADE",
            name="fk_pay_recommendation_components_rec",
        ),
        sa.UniqueConstraint(
            "recommendation_id",
            "component",
            name="uq_pay_recommendation_components_rec_component",
        ),
    )
    op.create_index(
        "ix_pay_recommendation_components_recommendation_id",
        "pay_recommendation_components",
        ["recommendation_id"],
    )

    # ---------------- pay_recommendation_overrides --------------------
    op.create_table(
        "pay_recommendation_overrides",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("recommendation_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("role_criticality", sa.String(length=16), nullable=True),
        sa.Column(
            "promotion_consideration",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["recommendation_id"],
            ["pay_recommendations.id"],
            ondelete="CASCADE",
            name="fk_pay_recommendation_overrides_rec",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_pay_recommendation_overrides_actor",
        ),
        sa.UniqueConstraint(
            "recommendation_id",
            "actor_user_id",
            name="uq_pay_recommendation_overrides_rec_actor",
        ),
    )
    op.create_index(
        "ix_pay_recommendation_overrides_recommendation_id",
        "pay_recommendation_overrides",
        ["recommendation_id"],
    )

    # ---------------- pay_recommendation_annotations ------------------
    op.create_table(
        "pay_recommendation_annotations",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("recommendation_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["recommendation_id"],
            ["pay_recommendations.id"],
            ondelete="CASCADE",
            name="fk_pay_recommendation_annotations_rec",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_pay_recommendation_annotations_actor",
        ),
    )
    op.create_index(
        "ix_pay_recommendation_annotations_recommendation_id",
        "pay_recommendation_annotations",
        ["recommendation_id"],
    )

    # ---------------- jvre_snapshots ----------------------------------
    op.create_table(
        "jvre_snapshots",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column("subject_user_id", sa.Uuid(), nullable=False),
        _money_column("recommended_base", nullable=True),
        _money_column("recommended_variable", nullable=True),
        _money_column("recommended_lti_fmv", nullable=True),
        sa.Column("recommended_lti_units", sa.Integer(), nullable=True),
        _money_column("recommended_other_rewards", nullable=True),
        _currency_column(),
        sa.Column("criticality", sa.String(length=32), nullable=True),
        sa.Column("market_position", sa.String(length=32), nullable=True),
        sa.Column(
            "promotion_readiness", sa.String(length=32), nullable=True
        ),
        sa.Column(
            "recommended_level", sa.String(length=32), nullable=True
        ),
        sa.Column("risk_callout_text", sa.Text(), nullable=True),
        sa.Column("ai_suggestion_text", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_jvre_snapshots_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"],
            ["compensation_cycles.id"],
            ondelete="CASCADE",
            name="fk_jvre_snapshots_cycle",
        ),
        sa.ForeignKeyConstraint(
            ["subject_user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_jvre_snapshots_subject",
        ),
        sa.UniqueConstraint(
            "cycle_id",
            "subject_user_id",
            name="uq_jvre_snapshots_cycle_subject",
        ),
    )
    op.create_index(
        "ix_jvre_snapshots_tenant_id", "jvre_snapshots", ["tenant_id"]
    )
    op.create_index(
        "ix_jvre_snapshots_cycle_id", "jvre_snapshots", ["cycle_id"]
    )
    op.create_index(
        "ix_jvre_snapshots_subject_user_id",
        "jvre_snapshots",
        ["subject_user_id"],
    )

    # ---------------- market_benchmarks -------------------------------
    op.create_table(
        "market_benchmarks",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_user_id", sa.Uuid(), nullable=False),
        _money_column("current_pay"),
        _money_column("target_pay"),
        _currency_column(),
        sa.Column("compa_ratio", _NUMERIC_RATIO, nullable=False),
        sa.Column(
            "target_compa_ratio_min", _NUMERIC_RATIO, nullable=True
        ),
        sa.Column(
            "target_compa_ratio_max", _NUMERIC_RATIO, nullable=True
        ),
        sa.Column("delta_status_text", sa.String(length=255), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_market_benchmarks_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["subject_user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_market_benchmarks_subject",
        ),
        sa.UniqueConstraint(
            "subject_user_id", name="uq_market_benchmarks_subject"
        ),
    )
    op.create_index(
        "ix_market_benchmarks_tenant_id",
        "market_benchmarks",
        ["tenant_id"],
    )

    # ---------------- compensation_history ----------------------------
    op.create_table(
        "compensation_history",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_user_id", sa.Uuid(), nullable=False),
        sa.Column("fy_label", sa.String(length=32), nullable=False),
        sa.Column("level_code", sa.String(length=32), nullable=True),
        _money_column("comp_change_amount", nullable=True),
        _currency_column(),
        sa.Column("perf_rating", sa.String(length=16), nullable=True),
        sa.Column(
            "was_promoted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
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
            name="fk_compensation_history_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["subject_user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_compensation_history_subject",
        ),
        sa.UniqueConstraint(
            "subject_user_id",
            "fy_label",
            name="uq_compensation_history_subject_fy",
        ),
    )
    op.create_index(
        "ix_compensation_history_tenant_id",
        "compensation_history",
        ["tenant_id"],
    )

    # ---------------- RLS on tenant-scoped tables ---------------------
    # FK-isolated tables (lines, components, overrides, annotations)
    # don't carry tenant_id, so the standard policy doesn't apply
    # there; they inherit isolation through the parent's RLS + the
    # cascade FK constraints. Everything else follows the same pattern
    # as ``departments``.
    for table in _TENANT_SCOPED_TABLES:
        if table in _FK_ISOLATED_TABLES:
            continue
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
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

    # ---------------- grants for the rls_tester role ------------------
    # Mirrors what tests/conftest.py grants on ``departments`` so the
    # RLS isolation test pattern extends to the new tenant-scoped
    # tables. The role is a no-op when it doesn't exist (production);
    # it only appears in test/dev databases. Wrap in DO so missing-role
    # is silently skipped.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rls_tester') THEN
                GRANT SELECT ON
                    compensation_cycles,
                    reporting_relationships,
                    budget_allocations,
                    budget_allocation_lines,
                    pay_recommendations,
                    pay_recommendation_components,
                    pay_recommendation_overrides,
                    pay_recommendation_annotations,
                    jvre_snapshots,
                    market_benchmarks,
                    compensation_history
                TO rls_tester;
            END IF;
        END
        $$
        """
    )


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------
def downgrade() -> None:
    # Drop policies first, then indexes (most are CASCADE'd by drop_table
    # but explicit is fine), then tables in reverse-dependency order.
    for table in _TENANT_SCOPED_TABLES:
        if table in _FK_ISOLATED_TABLES:
            continue
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")

    op.drop_table("compensation_history")
    op.drop_table("market_benchmarks")
    op.drop_table("jvre_snapshots")
    op.drop_table("pay_recommendation_annotations")
    op.drop_table("pay_recommendation_overrides")
    op.drop_table("pay_recommendation_components")
    op.drop_table("pay_recommendations")
    op.drop_table("budget_allocation_lines")
    op.drop_table("budget_allocations")
    op.drop_table("reporting_relationships")
    op.drop_table("compensation_cycles")

    # Remove the seeded role rows. Idempotent — if they're absent
    # (manual cleanup, etc.), DELETE is a no-op.
    op.execute("DELETE FROM roles WHERE code IN ('CHRO', 'CFO')")

    op.drop_column("tenants", "default_currency_code")
