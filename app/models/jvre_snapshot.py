"""JvreSnapshot ORM model.

Pre-computed JVRE recommendations for a (cycle, subject) pair. Acts as
the JVRE feed for v0.1 — there is no real engine integration; the
seed populates this table and the application reads from it. The real
JVRE engine in v0.2+ will write into the same table on a schedule;
the contract for swapping it in is "replace the producer, keep the
table shape".

One row per subject per cycle. Same shape carries both individual-
contributor recommendations (used to render IC cards) and
manager-level recommendations (used to size the MoM/MoP pools); the
consumer interprets components as "individual pay" or "pool sizing"
based on the subject's role.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    ForeignKey,
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


class JvreCriticality(enum.StrEnum):
    """Criticality classification chip on the screen."""

    CRITICAL = "CRITICAL"
    MODERATE_HIGH = "MODERATE_HIGH"
    LOW_RISK = "LOW_RISK"


class JvreMarketPosition(enum.StrEnum):
    """Market-position classification chip on the screen."""

    BELOW_MARKET = "BELOW_MARKET"
    MARKET_ALIGNED = "MARKET_ALIGNED"
    ABOVE_MARKET = "ABOVE_MARKET"


class JvrePromotionReadiness(enum.StrEnum):
    """Promotion-readiness classification chip on the screen."""

    READY = "READY"
    CANDIDATE = "CANDIDATE"
    NOT_READY = "NOT_READY"


class JvreSnapshot(Base):
    """Pre-computed JVRE recommendation for one subject in one cycle."""

    __tablename__ = "jvre_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "cycle_id",
            "subject_user_id",
            name="uq_jvre_snapshots_cycle_subject",
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

    # Recommended pay components (or pool sizes when the subject is a
    # manager). Nullable so the row can carry partial recommendations.
    recommended_base: Mapped[Decimal | None] = mapped_column(
        _MONEY, nullable=True
    )
    recommended_variable: Mapped[Decimal | None] = mapped_column(
        _MONEY, nullable=True
    )
    recommended_lti_fmv: Mapped[Decimal | None] = mapped_column(
        _MONEY, nullable=True
    )
    recommended_lti_units: Mapped[int | None] = mapped_column(nullable=True)
    recommended_other_rewards: Mapped[Decimal | None] = mapped_column(
        _MONEY, nullable=True
    )

    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD", server_default="USD"
    )

    criticality: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    market_position: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    promotion_readiness: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )

    # Recommended target level (e.g. "L5"). Surfaces as the "Recommended
    # Level: L4 → L5" callout when it differs from the subject's
    # current level.
    recommended_level: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )

    # Current-cycle pay actuals — seeded from comp history so the
    # rationale endpoint can compute increase percentages without a
    # join to compensation_history at query time.
    current_base: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    current_variable: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)

    # JVRE engine score (0–10) for the subject in this cycle.
    jvre_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    # Current-FY unvested LTI units, mirroring compensation_history.
    current_fy_vesting_units: Mapped[int | None] = mapped_column(nullable=True)

    # Free-text from the engine for the risk callout strip
    # ("3 of 8 engineers with confirmed external offers…") and the AI
    # Suggestion block beneath each card.
    risk_callout_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_suggestion_text: Mapped[str | None] = mapped_column(
        Text, nullable=True
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
            f"<JvreSnapshot subject={self.subject_user_id} "
            f"criticality={self.criticality!r}>"
        )
