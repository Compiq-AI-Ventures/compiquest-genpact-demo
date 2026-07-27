"""IquestEngineOutput ORM model.

One row per (cycle, subject) — the full output of the iQuest compensation
engine. Mirrors the external engine's CSV/spreadsheet output so the
backend can serve all signals to the frontend without re-computing them.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

_MONEY = Numeric(18, 2)
_SCORE = Numeric(6, 3)
_RATIO = Numeric(8, 4)
_PCT = Numeric(8, 4)


class IquestEngineOutput(Base):
    """Full iQuest engine output for one (cycle, subject)."""

    __tablename__ = "iquest_engine_output"
    __table_args__ = (
        UniqueConstraint(
            "cycle_id",
            "subject_user_id",
            name="uq_iquest_engine_output_cycle_subject",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("compensation_cycles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ---------------------------------------------------------------------------
    # Identity / org
    # ---------------------------------------------------------------------------
    employee_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    employee_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    department: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bu: Mapped[str | None] = mapped_column(String(128), nullable=True)
    job_family: Mapped[str | None] = mapped_column(String(128), nullable=True)
    job_role: Mapped[str | None] = mapped_column(String(128), nullable=True)
    band: Mapped[str | None] = mapped_column(String(64), nullable=True)
    designation: Mapped[str | None] = mapped_column(String(128), nullable=True)
    location: Mapped[str | None] = mapped_column(String(128), nullable=True)
    supervisor: Mapped[str | None] = mapped_column(String(256), nullable=True)
    doj: Mapped[date | None] = mapped_column(Date, nullable=True)
    tenure_years: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # ---------------------------------------------------------------------------
    # Performance
    # ---------------------------------------------------------------------------
    rating_band: Mapped[str | None] = mapped_column(String(32), nullable=True)
    potential_rating: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)
    perf_cycle: Mapped[str | None] = mapped_column(String(32), nullable=True)
    manager_criticality_score: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)

    # ---------------------------------------------------------------------------
    # Current pay
    # ---------------------------------------------------------------------------
    current_base_inr: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    target_bonus_pct: Mapped[Decimal | None] = mapped_column(_PCT, nullable=True)
    total_cash_inr: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    external_cr: Mapped[Decimal | None] = mapped_column(_RATIO, nullable=True)
    months_since_last_increase: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_of_replacement_inr: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)

    # ---------------------------------------------------------------------------
    # Market benchmarks
    # ---------------------------------------------------------------------------
    benchmark_family: Mapped[str | None] = mapped_column(String(128), nullable=True)
    effective_p50: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    benchmark_p25: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    benchmark_p50: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    benchmark_p75: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    benchmark_var_pct: Mapped[Decimal | None] = mapped_column(_PCT, nullable=True)
    ttf_months: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)
    open_hc: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ---------------------------------------------------------------------------
    # JVRE factor scores
    # ---------------------------------------------------------------------------
    macro_score: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)
    f1_macro_factor: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)
    f2_compa_factor: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)
    cr_gap_score: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)
    ttf_score: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)
    hc_score: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)
    hp_score: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)
    equity_cliff_score: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)
    exit_risk_score: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)
    criticality_score: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)
    f3_crit_factor: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)

    # ---------------------------------------------------------------------------
    # Equity
    # ---------------------------------------------------------------------------
    unvested_usd: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    equity_value_inr: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    next_vest_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    months_to_next_vest: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)

    # ---------------------------------------------------------------------------
    # Performance signals
    # ---------------------------------------------------------------------------
    perf_signal: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)
    exit_risk_signal: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)
    inc_lag_signal: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)
    tenure_signal: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)
    f4_perf_factor: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)

    # ---------------------------------------------------------------------------
    # JVRE output
    # ---------------------------------------------------------------------------
    jvre_score: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)
    jvre_tier: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # ---------------------------------------------------------------------------
    # Pay policy
    # ---------------------------------------------------------------------------
    pay_policy_pctile: Mapped[Decimal | None] = mapped_column(_SCORE, nullable=True)
    policy_target_cr: Mapped[Decimal | None] = mapped_column(_RATIO, nullable=True)
    target_cr: Mapped[Decimal | None] = mapped_column(_RATIO, nullable=True)
    target_tcc_inr: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)

    # ---------------------------------------------------------------------------
    # Recommendations
    # ---------------------------------------------------------------------------
    rec_new_base_inr: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    rec_increase_pct: Mapped[Decimal | None] = mapped_column(_PCT, nullable=True)
    new_cr_after_rec: Mapped[Decimal | None] = mapped_column(_RATIO, nullable=True)
    rec_var_pct: Mapped[Decimal | None] = mapped_column(_PCT, nullable=True)
    rec_total_cash_inr: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    scale_factor: Mapped[Decimal | None] = mapped_column(_RATIO, nullable=True)
    capped_rec_increase_pct: Mapped[Decimal | None] = mapped_column(_PCT, nullable=True)
    capped_new_base_inr: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    capped_var_pct: Mapped[Decimal | None] = mapped_column(_PCT, nullable=True)
    capped_total_cash_inr: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    rem_gap_to_policy_pctile: Mapped[Decimal | None] = mapped_column(_PCT, nullable=True)

    # ---------------------------------------------------------------------------
    # Flags
    # ---------------------------------------------------------------------------
    promotion_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    multi_cycle_plan_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    multi_cycle_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    funding_gap_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    band_ceiling_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    var_pay_alignment_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    band_c_review_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # ---------------------------------------------------------------------------
    # Pre-generated rationale
    # ---------------------------------------------------------------------------
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
