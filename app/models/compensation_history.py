"""CompensationHistory ORM model.

Per-subject historical comp data feeding the "Compensation &
Performance History" expansion block on a recommendation card. One
row per (subject, FY); the screen renders Current + last two FYs.

Out-of-scope notes
------------------
We don't carry the ingestion pipeline that populates this table — the
implementation team uploads historical data during onboarding via a
process external to this service. For v0.1 the seed script provides
synthetic data for the demo tenant.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

_MONEY = Numeric(18, 2)


class CompensationHistory(Base):
    """One historical FY row for one subject."""

    __tablename__ = "compensation_history"
    __table_args__ = (
        UniqueConstraint(
            "subject_user_id",
            "fy_label",
            name="uq_compensation_history_subject_fy",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    fy_label: Mapped[str] = mapped_column(String(32), nullable=False)
    level_code: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )

    # Comp change vs prior FY. NULL when not applicable (first FY in
    # the system, etc.). Stored as a signed amount.
    comp_change_amount: Mapped[Decimal | None] = mapped_column(
        _MONEY, nullable=True
    )
    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD", server_default="USD"
    )

    # 1-5 scale (or whatever the tenant uses); free-form short string
    # so "4/5", "Exceeds", "B+" all render unchanged.
    perf_rating: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )

    was_promoted: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<CompensationHistory subject={self.subject_user_id} "
            f"fy={self.fy_label!r}>"
        )


# Suppress an "imported but unused" lint when this module gains
# ``Integer``-typed columns in the future.
_ = Integer
