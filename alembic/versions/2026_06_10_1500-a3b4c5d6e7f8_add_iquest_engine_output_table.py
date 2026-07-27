"""add_iquest_engine_output_table

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-06-10 15:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3b4c5d6e7f8"
down_revision: str | Sequence[str] | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MONEY = sa.Numeric(18, 2)
_SCORE = sa.Numeric(6, 3)
_RATIO = sa.Numeric(8, 4)
_PCT = sa.Numeric(8, 4)


def upgrade() -> None:
    """Apply this migration."""
    op.create_table(
        "iquest_engine_output",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), sa.ForeignKey("compensation_cycles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        # Identity
        sa.Column("employee_id", sa.String(64), nullable=True),
        sa.Column("employee_name", sa.String(256), nullable=True),
        sa.Column("department", sa.String(128), nullable=True),
        sa.Column("bu", sa.String(128), nullable=True),
        sa.Column("job_family", sa.String(128), nullable=True),
        sa.Column("job_role", sa.String(128), nullable=True),
        sa.Column("band", sa.String(64), nullable=True),
        sa.Column("designation", sa.String(128), nullable=True),
        sa.Column("location", sa.String(128), nullable=True),
        sa.Column("supervisor", sa.String(256), nullable=True),
        sa.Column("doj", sa.Date(), nullable=True),
        sa.Column("tenure_years", _SCORE, nullable=True),
        sa.Column("gender", sa.String(32), nullable=True),
        # Performance
        sa.Column("rating_band", sa.String(32), nullable=True),
        sa.Column("potential_rating", _SCORE, nullable=True),
        sa.Column("perf_cycle", sa.String(32), nullable=True),
        sa.Column("manager_criticality_score", _SCORE, nullable=True),
        # Current pay
        sa.Column("current_base_inr", _MONEY, nullable=True),
        sa.Column("target_bonus_pct", _PCT, nullable=True),
        sa.Column("total_cash_inr", _MONEY, nullable=True),
        sa.Column("external_cr", _RATIO, nullable=True),
        sa.Column("months_since_last_increase", sa.Integer(), nullable=True),
        sa.Column("cost_of_replacement_inr", _MONEY, nullable=True),
        # Market benchmarks
        sa.Column("benchmark_family", sa.String(128), nullable=True),
        sa.Column("effective_p50", _MONEY, nullable=True),
        sa.Column("benchmark_p25", _MONEY, nullable=True),
        sa.Column("benchmark_p50", _MONEY, nullable=True),
        sa.Column("benchmark_p75", _MONEY, nullable=True),
        sa.Column("benchmark_var_pct", _PCT, nullable=True),
        sa.Column("ttf_months", _SCORE, nullable=True),
        sa.Column("open_hc", sa.Integer(), nullable=True),
        # JVRE factors
        sa.Column("macro_score", _SCORE, nullable=True),
        sa.Column("f1_macro_factor", _SCORE, nullable=True),
        sa.Column("f2_compa_factor", _SCORE, nullable=True),
        sa.Column("cr_gap_score", _SCORE, nullable=True),
        sa.Column("ttf_score", _SCORE, nullable=True),
        sa.Column("hc_score", _SCORE, nullable=True),
        sa.Column("hp_score", _SCORE, nullable=True),
        sa.Column("equity_cliff_score", _SCORE, nullable=True),
        sa.Column("exit_risk_score", _SCORE, nullable=True),
        sa.Column("criticality_score", _SCORE, nullable=True),
        sa.Column("f3_crit_factor", _SCORE, nullable=True),
        # Equity
        sa.Column("unvested_usd", _MONEY, nullable=True),
        sa.Column("equity_value_inr", _MONEY, nullable=True),
        sa.Column("next_vest_date", sa.Date(), nullable=True),
        sa.Column("months_to_next_vest", _SCORE, nullable=True),
        # Performance signals
        sa.Column("perf_signal", _SCORE, nullable=True),
        sa.Column("exit_risk_signal", _SCORE, nullable=True),
        sa.Column("inc_lag_signal", _SCORE, nullable=True),
        sa.Column("tenure_signal", _SCORE, nullable=True),
        sa.Column("f4_perf_factor", _SCORE, nullable=True),
        # JVRE output
        sa.Column("jvre_score", _SCORE, nullable=True),
        sa.Column("jvre_tier", sa.String(32), nullable=True),
        # Pay policy
        sa.Column("pay_policy_pctile", _SCORE, nullable=True),
        sa.Column("policy_target_cr", _RATIO, nullable=True),
        sa.Column("target_cr", _RATIO, nullable=True),
        sa.Column("target_tcc_inr", _MONEY, nullable=True),
        # Recommendations
        sa.Column("rec_new_base_inr", _MONEY, nullable=True),
        sa.Column("rec_increase_pct", _PCT, nullable=True),
        sa.Column("new_cr_after_rec", _RATIO, nullable=True),
        sa.Column("rec_var_pct", _PCT, nullable=True),
        sa.Column("rec_total_cash_inr", _MONEY, nullable=True),
        sa.Column("scale_factor", _RATIO, nullable=True),
        sa.Column("capped_rec_increase_pct", _PCT, nullable=True),
        sa.Column("capped_new_base_inr", _MONEY, nullable=True),
        sa.Column("capped_var_pct", _PCT, nullable=True),
        sa.Column("capped_total_cash_inr", _MONEY, nullable=True),
        sa.Column("rem_gap_to_policy_pctile", _PCT, nullable=True),
        # Flags
        sa.Column("promotion_flag", sa.Boolean(), nullable=True),
        sa.Column("multi_cycle_plan_flag", sa.Boolean(), nullable=True),
        sa.Column("multi_cycle_flag", sa.Boolean(), nullable=True),
        sa.Column("funding_gap_flag", sa.Boolean(), nullable=True),
        sa.Column("band_ceiling_flag", sa.Boolean(), nullable=True),
        sa.Column("var_pay_alignment_flag", sa.Boolean(), nullable=True),
        sa.Column("band_c_review_flag", sa.Boolean(), nullable=True),
        # Rationale
        sa.Column("rationale", sa.Text(), nullable=True),
        # Timestamps
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cycle_id", "subject_user_id", name="uq_iquest_engine_output_cycle_subject"),
    )
    op.create_index("ix_iquest_engine_output_tenant_id", "iquest_engine_output", ["tenant_id"])
    op.create_index("ix_iquest_engine_output_cycle_id", "iquest_engine_output", ["cycle_id"])
    op.create_index("ix_iquest_engine_output_subject_user_id", "iquest_engine_output", ["subject_user_id"])


def downgrade() -> None:
    """Revert this migration."""
    op.drop_index("ix_iquest_engine_output_subject_user_id", table_name="iquest_engine_output")
    op.drop_index("ix_iquest_engine_output_cycle_id", table_name="iquest_engine_output")
    op.drop_index("ix_iquest_engine_output_tenant_id", table_name="iquest_engine_output")
    op.drop_table("iquest_engine_output")
