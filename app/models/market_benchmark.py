"""MarketBenchmark ORM model.

Per-subject market data feeding the COMPA Ratio gauge that expands
under a recommendation card when the actor edits a cell. One row per
(tenant, subject) — the most recent benchmark applies for the cycle
in flight. Historical benchmarks aren't tracked yet; if needed they
can move to a snapshot table later without affecting consumers.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    ForeignKey,
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
# COMPA ratios are dimensionless multipliers, conventionally rendered
# to 2 decimal places ("0.80", "1.05"). NUMERIC(5, 2) gives 999.99
# headroom — way more than COMPA ever needs.
_RATIO = Numeric(5, 2)


class MarketBenchmark(Base):
    """Market-pay reference for one subject."""

    __tablename__ = "market_benchmarks"
    __table_args__ = (
        UniqueConstraint(
            "subject_user_id", name="uq_market_benchmarks_subject"
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

    current_pay: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    target_pay: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD", server_default="USD"
    )

    compa_ratio: Mapped[Decimal] = mapped_column(_RATIO, nullable=False)
    target_compa_ratio_min: Mapped[Decimal | None] = mapped_column(
        _RATIO, nullable=True
    )
    target_compa_ratio_max: Mapped[Decimal | None] = mapped_column(
        _RATIO, nullable=True
    )

    # Free text shown on the gauge ("Under Target by 9%"). Computed
    # upstream so the engine can phrase it; the API just passes it
    # through.
    delta_status_text: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<MarketBenchmark subject={self.subject_user_id} "
            f"compa={self.compa_ratio}>"
        )
