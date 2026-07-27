"""Pay recommendation models.

Four tables packaged together because the recommendation card on the
screen reads from all of them in lockstep:

* :class:`PayRecommendation` — header row. One per (cycle, actor,
  subject, relationship_kind). Carries status + timestamps + parent
  link (when the MoM's review record points back at the MoP's
  submission).
* :class:`PayRecommendationComponent` — per-pay-component lineage.
  One row per component (BASE_PAY, VARIABLE_PAY, LTI_GRANT_FMV,
  OTHER_REWARDS, LTI_UNITS) per recommendation. Stores all of:
  ``current_value``, ``jvre_rec_value``, ``mgr_rec_value`` (MoP's
  submission), ``mom_rec_value`` (MoM's override on top, NULL until
  set). The screen renders all three side-by-side without joins.
* :class:`PayRecommendationOverride` — once-per-(rec, actor) metadata
  captured when an actor first overrides JVRE: the reason, the role
  criticality, the promotion-consideration flag.
* :class:`PayRecommendationAnnotation` — append-only narrative feed
  ("Rahul's Action: Promotion proposed and Base pay adjusted to close
  the market gap.") attached to a recommendation. Distinct from the
  ``audit_logs`` table — annotations are user-facing; audit_logs are
  operational.

Relationship-kind semantics
---------------------------
* ``MGR_FOR_IC``              — MoP recommends pay for an IC.
* ``MOM_FOR_MGR``             — MoM recommends pay for an MoP (the
  MoP's own pay package, not a roll-up).
* ``MOM_REVIEWS_MGR_FOR_IC``  — MoM's review record on top of an
  ``MGR_FOR_IC`` submission. ``parent_recommendation_id`` points at
  the MoP's row.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
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


class PayRecommendationStatus(enum.StrEnum):
    """Allowed values for :attr:`PayRecommendation.status`."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REVISED = "REVISED"


class PayRecommendationRelationshipKind(enum.StrEnum):
    """Who-is-recommending-for-whom flavor of a row."""

    MGR_FOR_IC = "MGR_FOR_IC"
    MOM_FOR_MGR = "MOM_FOR_MGR"
    MOM_REVIEWS_MGR_FOR_IC = "MOM_REVIEWS_MGR_FOR_IC"


class PayComponent(enum.StrEnum):
    """Allowed values for :attr:`PayRecommendationComponent.component`.

    Five components per recommendation: four monetary (Base / Variable
    / LTI FMV / Other) and one count (LTI Units, paired with the FMV
    so the screen can render "$10,000 / 10 Units").
    """

    BASE_PAY = "BASE_PAY"
    VARIABLE_PAY = "VARIABLE_PAY"
    LTI_GRANT_FMV = "LTI_GRANT_FMV"
    OTHER_REWARDS = "OTHER_REWARDS"
    LTI_UNITS = "LTI_UNITS"


class RoleCriticality(enum.StrEnum):
    """Allowed values for :attr:`PayRecommendationOverride.role_criticality`."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


# ---------------------------------------------------------------------------
# PayRecommendation
# ---------------------------------------------------------------------------
class PayRecommendation(Base):
    """One actor's recommendation for one subject within one cycle."""

    __tablename__ = "pay_recommendations"
    __table_args__ = (
        UniqueConstraint(
            "cycle_id",
            "actor_user_id",
            "subject_user_id",
            "relationship_kind",
            name="uq_pay_recommendations_cycle_actor_subject_kind",
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

    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The MoM's review record points back at the MoP's submission so
    # the provenance chain is queryable.
    parent_recommendation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("pay_recommendations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    relationship_kind: Mapped[str] = mapped_column(
        String(48), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=PayRecommendationStatus.DRAFT.value,
        server_default=PayRecommendationStatus.DRAFT.value,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD", server_default="USD"
    )

    submitted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Stamped when the actor clicks "Save & Next" on the card. Distinct
    # from ``updated_at`` (every cell change fires onupdate) and from
    # ``status`` (which only flips on submit). The "1 of N Completed"
    # counter on the screen counts rows with ``saved_at IS NOT NULL``.
    saved_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
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
            f"<PayRecommendation actor={self.actor_user_id} "
            f"subject={self.subject_user_id} "
            f"kind={self.relationship_kind!r} status={self.status!r}>"
        )


# ---------------------------------------------------------------------------
# PayRecommendationComponent
# ---------------------------------------------------------------------------
class PayRecommendationComponent(Base):
    """Per-component lineage for one recommendation.

    Stores the full provenance of each editable field so the screen
    can render My Rec / MGR Rec / JVRE Rec / Current side-by-side
    without joining anywhere. ``final_value`` is computed in code (
    ``mom_rec_value`` if not NULL else ``mgr_rec_value`` if not NULL
    else ``jvre_rec_value``).

    For ``LTI_UNITS`` the values represent integer unit counts; the
    paired ``LTI_GRANT_FMV`` row carries the dollar value. We store
    them as NUMERIC for column uniformity and cast to int in the
    service layer.
    """

    __tablename__ = "pay_recommendation_components"
    __table_args__ = (
        UniqueConstraint(
            "recommendation_id",
            "component",
            name="uq_pay_recommendation_components_rec_component",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("pay_recommendations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    component: Mapped[str] = mapped_column(String(32), nullable=False)

    current_value: Mapped[Decimal | None] = mapped_column(
        _MONEY, nullable=True
    )
    jvre_rec_value: Mapped[Decimal | None] = mapped_column(
        _MONEY, nullable=True
    )
    mgr_rec_value: Mapped[Decimal | None] = mapped_column(
        _MONEY, nullable=True
    )
    mom_rec_value: Mapped[Decimal | None] = mapped_column(
        _MONEY, nullable=True
    )

    # Carried per-component so the LTI_UNITS row can sit in the same
    # table without warping its meaning. For monetary components this
    # matches the parent recommendation's currency_code.
    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD", server_default="USD"
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


# ---------------------------------------------------------------------------
# PayRecommendationOverride
# ---------------------------------------------------------------------------
class PayRecommendationOverride(Base):
    """Override metadata captured the first time an actor moves off JVRE.

    One row per (recommendation, actor). Subsequent edits update the
    same row rather than appending a new one — the rationale is the
    actor's, not per-keystroke.
    """

    __tablename__ = "pay_recommendation_overrides"
    __table_args__ = (
        UniqueConstraint(
            "recommendation_id",
            "actor_user_id",
            name="uq_pay_recommendation_overrides_rec_actor",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("pay_recommendations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Free-text reason code from a small lookup; stored as a string so
    # additions don't require a migration.
    reason_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    role_criticality: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    promotion_consideration: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
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


# ---------------------------------------------------------------------------
# PayRecommendationAnnotation
# ---------------------------------------------------------------------------
class PayRecommendationAnnotation(Base):
    """Append-only user-facing note attached to a recommendation.

    Renders as the "Rahul's Action: …" / "Christy's action: …" strips
    on the cards. Distinct from ``audit_logs`` (which is operational
    history); these are content the actor authors and other actors
    read.
    """

    __tablename__ = "pay_recommendation_annotations"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("pay_recommendations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


# Re-exported so future ``Integer``-typed columns elsewhere in the
# package don't trigger an unused-import lint.
_ = Integer
