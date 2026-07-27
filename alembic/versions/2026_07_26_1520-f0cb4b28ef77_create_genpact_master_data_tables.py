"""create genpact master-data tables

Revision ID: f0cb4b28ef77
Revises: c7d8e9f0a1b2
Create Date: 2026-07-26 15:20:45.315094+00:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f0cb4b28ef77"
down_revision: Union[str, Sequence[str], None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None




def upgrade() -> None:
    """Create the Genpact F&A master-data / analytics tables."""
    op.create_table(
        "genpact_ai_impact",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("industry", sa.String(length=64), nullable=False),
        sa.Column("job_family", sa.String(length=64), nullable=False),
        sa.Column("geography", sa.String(length=64), nullable=False),
        sa.Column("ai_exposure_score", sa.Integer(), nullable=False),
        sa.Column("demand_outlook", sa.String(length=64), nullable=False),
        sa.Column("net_talent_position", sa.String(length=64), nullable=False),
        sa.Column("how_ai_reshapes_role", sa.Text(), nullable=False),
        sa.Column("annual_graduate_supply", sa.BigInteger(), nullable=False),
        sa.Column("projected_supply_2030", sa.BigInteger(), nullable=False),
        sa.Column("supply_vs_demand_signal", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_genpact_ai_impact_tenant_id"), "genpact_ai_impact", ["tenant_id"], unique=False
    )
    op.create_table(
        "genpact_benchmark",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("benchmark_source", sa.String(length=64), nullable=False),
        sa.Column("business_unit", sa.String(length=64), nullable=False),
        sa.Column("department", sa.String(length=64), nullable=False),
        sa.Column("job_family", sa.String(length=64), nullable=False),
        sa.Column("designation", sa.String(length=128), nullable=False),
        sa.Column("job_level", sa.String(length=8), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("geography", sa.String(length=64), nullable=False),
        sa.Column("survey_source", sa.String(length=96), nullable=False),
        sa.Column("survey_year", sa.Integer(), nullable=False),
        sa.Column("base_p25", sa.BigInteger(), nullable=False),
        sa.Column("base_p50", sa.BigInteger(), nullable=False),
        sa.Column("base_p75", sa.BigInteger(), nullable=False),
        sa.Column("base_p90", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_genpact_benchmark_tenant_id"), "genpact_benchmark", ["tenant_id"], unique=False
    )
    op.create_table(
        "genpact_comp_outlook",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("industry", sa.String(length=64), nullable=False),
        sa.Column("job_family", sa.String(length=64), nullable=False),
        sa.Column("geography", sa.String(length=64), nullable=False),
        sa.Column("salary_band_mid_level", sa.String(length=96), nullable=False),
        sa.Column("typical_increment_2025_pct", sa.Numeric(precision=8, scale=3), nullable=False),
        sa.Column("projected_increment_2027_pct", sa.Numeric(precision=8, scale=3), nullable=False),
        sa.Column("increment_delta", sa.Numeric(precision=8, scale=3), nullable=False),
        sa.Column("compensation_trend", sa.String(length=64), nullable=False),
        sa.Column("ai_skills_premium_pct", sa.Numeric(precision=8, scale=3), nullable=False),
        sa.Column("demand_outlook", sa.String(length=64), nullable=False),
        sa.Column("net_talent_position", sa.String(length=64), nullable=False),
        sa.Column("compensation_narrative", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_genpact_comp_outlook_tenant_id"),
        "genpact_comp_outlook",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "genpact_currency_master",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("reporting_cycle", sa.String(length=16), nullable=False),
        sa.Column("reporting_currency", sa.String(length=3), nullable=False),
        sa.Column("local_currency", sa.String(length=3), nullable=False),
        sa.Column("conversion_value", sa.Numeric(precision=14, scale=6), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_genpact_currency_master_tenant_id"),
        "genpact_currency_master",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "genpact_employee_master",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("employee_id", sa.String(length=16), nullable=False),
        sa.Column("employee_name", sa.String(length=128), nullable=False),
        sa.Column("fiscal_year", sa.String(length=16), nullable=False),
        sa.Column("job_position_id", sa.String(length=64), nullable=False),
        sa.Column("employment_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("exit_classification", sa.String(length=64), nullable=False),
        sa.Column("exit_date", sa.Date(), nullable=False),
        sa.Column("joining_date", sa.Date(), nullable=False),
        sa.Column("gender", sa.String(length=32), nullable=False),
        sa.Column("business_unit", sa.String(length=64), nullable=False),
        sa.Column("department", sa.String(length=64), nullable=False),
        sa.Column("job_family", sa.String(length=64), nullable=False),
        sa.Column("designation", sa.String(length=128), nullable=False),
        sa.Column("job_level", sa.String(length=8), nullable=False),
        sa.Column("previous_job_level", sa.String(length=8), nullable=False),
        sa.Column("manager_employee_id", sa.String(length=16), nullable=False),
        sa.Column("manager_job_band", sa.String(length=8), nullable=False),
        sa.Column("span_direct", sa.Integer(), nullable=False),
        sa.Column("span_indirect", sa.Integer(), nullable=False),
        sa.Column("location_city", sa.String(length=96), nullable=False),
        sa.Column("work_mode", sa.String(length=16), nullable=False),
        sa.Column("performance_rating", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("performance_rating_category", sa.String(length=64), nullable=False),
        sa.Column("promotion_flag", sa.Boolean(), nullable=False),
        sa.Column("last_promotion_date", sa.Date(), nullable=False),
        sa.Column("company_experience_years", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("total_experience_years_1", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("base_salary_pre", sa.BigInteger(), nullable=False),
        sa.Column("base_salary_post", sa.BigInteger(), nullable=False),
        sa.Column("base_increment_pct", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("target_variable_pct", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("variable_pre", sa.BigInteger(), nullable=False),
        sa.Column("variable_post", sa.BigInteger(), nullable=False),
        sa.Column("variable_increment_pct", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("total_increment_pct", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("tcc_pre", sa.BigInteger(), nullable=False),
        sa.Column("tcc_post", sa.BigInteger(), nullable=False),
        sa.Column("total_rewards_pre", sa.BigInteger(), nullable=False),
        sa.Column("total_rewards_post", sa.BigInteger(), nullable=False),
        sa.Column("lti_eligible", sa.Boolean(), nullable=False),
        sa.Column("lti_grant_value", sa.BigInteger(), nullable=False),
        sa.Column("lti_type", sa.String(length=32), nullable=False),
        sa.Column("grant_date", sa.Date(), nullable=False),
        sa.Column("vesting_total_years", sa.Integer(), nullable=False),
        sa.Column("vesting_cliff_years", sa.Integer(), nullable=False),
        sa.Column("lti_unvested_remaining", sa.BigInteger(), nullable=False),
        sa.Column("next_vesting_date", sa.Date(), nullable=False),
        sa.Column("total_experience_years_2", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("promotion_flag_upcoming", sa.Boolean(), nullable=False),
        sa.Column("post_promotion_level", sa.String(length=8), nullable=False),
        sa.Column("post_promotion_department", sa.String(length=64), nullable=False),
        sa.Column("internal_compa_pre", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("internal_compa_post", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("external_compa_pre", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("external_compa_post", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_genpact_employee_master_tenant_id"),
        "genpact_employee_master",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "genpact_job_posting",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("position_id", sa.String(length=64), nullable=False),
        sa.Column("leaver_employee_id", sa.String(length=16), nullable=False),
        sa.Column("resignation_date", sa.Date(), nullable=False),
        sa.Column("posting_date", sa.Date(), nullable=False),
        sa.Column("offer_accept_date", sa.Date(), nullable=False),
        sa.Column("offers_extended", sa.Integer(), nullable=False),
        sa.Column("offer_outcome", sa.String(length=32), nullable=False),
        sa.Column("date_of_joining_replacement", sa.Date(), nullable=False),
        sa.Column("pct_hike_over_previous_job", sa.String(length=32), nullable=False),
        sa.Column("agency_fee_paid", sa.BigInteger(), nullable=False),
        sa.Column("recruiter_cost_estimate", sa.BigInteger(), nullable=False),
        sa.Column("onboarding_cost", sa.BigInteger(), nullable=False),
        sa.Column("leaver_last_working_day", sa.Date(), nullable=False),
        sa.Column("leaver_base_salary", sa.BigInteger(), nullable=False),
        sa.Column("hire_type", sa.String(length=32), nullable=False),
        sa.Column("notice_period_days", sa.Integer(), nullable=False),
        sa.Column("ttf_days", sa.Integer(), nullable=False),
        sa.Column("new_hire_base_salary", sa.BigInteger(), nullable=False),
        sa.Column("vacancy_gap_days", sa.Integer(), nullable=False),
        sa.Column("salary_premium", sa.BigInteger(), nullable=False),
        sa.Column("business_unit", sa.String(length=64), nullable=False),
        sa.Column("job_family", sa.String(length=64), nullable=False),
        sa.Column("job_level", sa.String(length=8), nullable=False),
        sa.Column("exit_reason", sa.String(length=96), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_genpact_job_posting_tenant_id"), "genpact_job_posting", ["tenant_id"], unique=False
    )
    op.create_table(
        "genpact_pay_policy_raw",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("row_num", sa.Integer(), nullable=False),
        sa.Column("c1", sa.String(length=512), nullable=False),
        sa.Column("c2", sa.String(length=512), nullable=False),
        sa.Column("c3", sa.String(length=512), nullable=False),
        sa.Column("c4", sa.String(length=512), nullable=False),
        sa.Column("c5", sa.String(length=512), nullable=False),
        sa.Column("c6", sa.String(length=512), nullable=False),
        sa.Column("c7", sa.String(length=512), nullable=False),
        sa.Column("c8", sa.String(length=512), nullable=False),
        sa.Column("c9", sa.String(length=512), nullable=False),
        sa.Column("c10", sa.String(length=512), nullable=False),
        sa.Column("c11", sa.String(length=512), nullable=False),
        sa.Column("c12", sa.String(length=512), nullable=False),
        sa.Column("c13", sa.String(length=512), nullable=False),
        sa.Column("c14", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_genpact_pay_policy_raw_tenant_id"),
        "genpact_pay_policy_raw",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "genpact_talent_supply",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("industry", sa.String(length=64), nullable=False),
        sa.Column("job_family", sa.String(length=64), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=False),
        sa.Column("core_discipline", sa.String(length=128), nullable=False),
        sa.Column("annual_graduates_india", sa.BigInteger(), nullable=False),
        sa.Column("projected_supply_2030", sa.BigInteger(), nullable=False),
        sa.Column("assumptions_notes", sa.Text(), nullable=False),
        sa.Column("implied_cagr_pct", sa.Numeric(precision=8, scale=3), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_genpact_talent_supply_tenant_id"),
        "genpact_talent_supply",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the Genpact F&A master-data / analytics tables."""
    op.drop_table("genpact_ai_impact")
    op.drop_table("genpact_benchmark")
    op.drop_table("genpact_comp_outlook")
    op.drop_table("genpact_currency_master")
    op.drop_table("genpact_employee_master")
    op.drop_table("genpact_job_posting")
    op.drop_table("genpact_pay_policy_raw")
    op.drop_table("genpact_talent_supply")
