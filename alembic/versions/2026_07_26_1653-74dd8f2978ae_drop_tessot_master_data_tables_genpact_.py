"""drop tessot master-data tables (genpact demo)

Revision ID: 74dd8f2978ae
Revises: f0cb4b28ef77
Create Date: 2026-07-26 16:53:19.255561+00:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "74dd8f2978ae"
down_revision: Union[str, Sequence[str], None] = "f0cb4b28ef77"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None




def upgrade() -> None:
    """Drop the Tessot master-data tables — iQuest AI now reads Genpact data."""
    op.drop_table("tessot_base_data")
    op.drop_table("tessot_financial_data")
    op.drop_table("tessot_policy")
    op.drop_table("tessot_job_posting_data")


def downgrade() -> None:
    """Recreate the Tessot master-data tables."""
    op.create_table(
        "tessot_base_data",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.String(length=16), server_default="", nullable=False),
        sa.Column("employee_name", sa.String(length=128), server_default="", nullable=False),
        sa.Column("fiscal_year", sa.Integer(), server_default="0", nullable=False),
        sa.Column("job_position_id", sa.String(length=64), server_default="", nullable=False),
        sa.Column("employment_type", sa.String(length=64), server_default="", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="", nullable=False),
        sa.Column("exit_classification", sa.String(length=32), server_default="", nullable=False),
        sa.Column("exit_date", sa.Date(), server_default=sa.text("'1970-01-01'"), nullable=False),
        sa.Column("joining_date", sa.Date(), server_default=sa.text("'1970-01-01'"), nullable=False),
        sa.Column("gender", sa.String(length=32), server_default="", nullable=False),
        sa.Column("business_unit", sa.String(length=64), server_default="", nullable=False),
        sa.Column("department", sa.String(length=64), server_default="", nullable=False),
        sa.Column("job_family", sa.String(length=64), server_default="", nullable=False),
        sa.Column("designation", sa.String(length=64), server_default="", nullable=False),
        sa.Column("job_level", sa.String(length=8), server_default="", nullable=False),
        sa.Column("previous_job_level", sa.String(length=8), server_default="", nullable=False),
        sa.Column("manager_employee_id", sa.String(length=16), server_default="", nullable=False),
        sa.Column("manager_job_band", sa.String(length=8), server_default="", nullable=False),
        sa.Column("span_direct", sa.Integer(), server_default="0", nullable=False),
        sa.Column("span_indirect", sa.Integer(), server_default="0", nullable=False),
        sa.Column("location_city", sa.String(length=64), server_default="", nullable=False),
        sa.Column("work_mode", sa.String(length=16), server_default="", nullable=False),
        sa.Column("performance_rating", sa.Numeric(precision=5, scale=2), server_default="0", nullable=False),
        sa.Column("promotion_flag", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("last_promotion_date", sa.Date(), server_default=sa.text("'1970-01-01'"), nullable=False),
        sa.Column("company_experience", sa.Numeric(precision=6, scale=2), server_default="0", nullable=False),
        sa.Column("total_experience", sa.Numeric(precision=6, scale=2), server_default="0", nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="", nullable=False),
        sa.Column("base_salary", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("target_bonus_pct", sa.Numeric(precision=8, scale=4), server_default="0", nullable=False),
        sa.Column("actual_bonus_paid", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("total_cash_compensation", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("total_rewards", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("pct_merit_increase", sa.Numeric(precision=8, scale=4), server_default="0", nullable=False),
        sa.Column("pct_correction_increase", sa.Numeric(precision=8, scale=4), server_default="0", nullable=False),
        sa.Column("pct_base_increase", sa.Numeric(precision=8, scale=4), server_default="0", nullable=False),
        sa.Column("total_increment_percent", sa.Numeric(precision=8, scale=4), server_default="0", nullable=False),
        sa.Column("lti_eligible", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("lti_grant_value", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("lti_type", sa.String(length=32), server_default="", nullable=False),
        sa.Column("grant_date", sa.Date(), server_default=sa.text("'1970-01-01'"), nullable=False),
        sa.Column("vesting_total_years", sa.Integer(), server_default="0", nullable=False),
        sa.Column("vesting_cliff_years", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lti_unvested_remaining", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("next_vesting_date", sa.Date(), server_default=sa.text("'1970-01-01'"), nullable=False),
        sa.Column("internal_compa_ratio", sa.Numeric(precision=8, scale=4), server_default="0", nullable=False),
        sa.Column("external_compa_ratio", sa.Numeric(precision=8, scale=4), server_default="0", nullable=False),
        sa.Column("benchmark_p25", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("benchmark_p50", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("benchmark_p75", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("benchmark_p90", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tessot_base_data_tenant_id"), "tessot_base_data", ["tenant_id"], unique=False)
    op.create_table(
        "tessot_financial_data",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("fy", sa.String(length=8), server_default="", nullable=False),
        sa.Column("unit", sa.String(length=32), server_default="", nullable=False),
        sa.Column("name_of_unit", sa.String(length=64), server_default="", nullable=False),
        sa.Column("nature_of_unit", sa.String(length=32), server_default="", nullable=False),
        sa.Column("revenue_inr_cr", sa.Numeric(precision=14, scale=2), server_default="0", nullable=False),
        sa.Column("profit_before_tax_inr_cr", sa.Numeric(precision=14, scale=2), server_default="0", nullable=False),
        sa.Column("headcount_fte", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tessot_financial_data_tenant_id"), "tessot_financial_data", ["tenant_id"], unique=False)
    op.create_table(
        "tessot_policy",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("job_family", sa.String(length=64), server_default="", nullable=False),
        sa.Column("job_function_bu", sa.String(length=64), server_default="", nullable=False),
        sa.Column("level_band", sa.String(length=8), server_default="", nullable=False),
        sa.Column("designation", sa.String(length=64), server_default="", nullable=False),
        sa.Column("min_inr", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("benchmark_p25_inr", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("benchmark_p50_inr", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("benchmark_p75_inr", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("benchmark_p90_inr", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("max_inr", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tessot_policy_tenant_id"), "tessot_policy", ["tenant_id"], unique=False)
    op.create_table(
        "tessot_job_posting_data",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("position_id", sa.String(length=64), server_default="", nullable=False),
        sa.Column("leaver_employee_id", sa.String(length=16), server_default="", nullable=False),
        sa.Column("resignation_date", sa.Date(), server_default=sa.text("'1970-01-01'"), nullable=False),
        sa.Column("posting_date", sa.Date(), server_default=sa.text("'1970-01-01'"), nullable=False),
        sa.Column("offer_accept_date", sa.Date(), server_default=sa.text("'1970-01-01'"), nullable=False),
        sa.Column("offers_extended", sa.Integer(), server_default="0", nullable=False),
        sa.Column("offer_outcome", sa.String(length=16), server_default="", nullable=False),
        sa.Column("date_of_joining_replacement", sa.Date(), server_default=sa.text("'1970-01-01'"), nullable=False),
        sa.Column("pct_hike_over_previous_job", sa.String(length=8), server_default="", nullable=False),
        sa.Column("agency_fee_paid", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("recruiter_cost_estimate", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("onboarding_cost", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("leaver_last_working_day", sa.Date(), server_default=sa.text("'1970-01-01'"), nullable=False),
        sa.Column("leaver_base_salary", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("hire_type", sa.String(length=16), server_default="", nullable=False),
        sa.Column("notice_period_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ttf_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("new_hire_base_salary", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("vacancy_gap_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("salary_premium", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tessot_job_posting_data_tenant_id"), "tessot_job_posting_data", ["tenant_id"], unique=False)
