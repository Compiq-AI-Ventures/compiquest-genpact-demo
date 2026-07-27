"""CompensationCycle ORM model.

A compensation cycle is the per-fiscal-year container for everything
the JVRE workspace operates on: budget allocations cascade inside it,
pay recommendations are scoped to it, JVRE snapshots are keyed against
it. One cycle per (tenant, FY).

Status semantics
----------------
* ``DRAFT``  — created but not yet open. Writes blocked.
* ``ACTIVE`` — in flight; managers and reviewers can act.
* ``CLOSED`` — terminal. Everything becomes read-only.

The status is enforced by application code rather than by a DB CHECK
constraint so we can introduce intermediate states (``LOCKED_FOR_REVIEW``
etc.) later without a migration.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CompensationCycleStatus(enum.StrEnum):
    """Allowed values for :attr:`CompensationCycle.status`."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class CompensationCycle(Base):
    """One fiscal-year compensation cycle for a tenant."""

    __tablename__ = "compensation_cycles"
    __table_args__ = (
        # Every (tenant, FY) is unique — no two cycles for the same
        # FY in the same tenant.
        UniqueConstraint("tenant_id", "fy_label", name="uq_compensation_cycles_tenant_fy"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Human-readable FY label, e.g. "FY2026". Free text so different
    # tenants can use their own conventions ("FY26", "2026-2027", etc.).
    fy_label: Mapped[str] = mapped_column(String(32), nullable=False)

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=CompensationCycleStatus.DRAFT.value,
        server_default=CompensationCycleStatus.DRAFT.value,
    )

    # When MoP / MoM submissions are due. Pure metadata — enforcement
    # (warnings, escalations) is at the application layer.
    submission_deadline: Mapped[date | None] = mapped_column(
        nullable=True,
    )

    # Currency for monetary columns within this cycle. Defaults to the
    # tenant's default at create time but stored per-cycle so historical
    # cycles preserve the currency they ran in.
    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="USD",
        server_default="USD",
    )

    # JVRE-alignment tolerance for the "JVRE Aligned" badge. Stored
    # per-cycle so admins can tighten/loosen without touching code.
    # Stored as a fraction (0.005 = 0.5%).
    jvre_alignment_tolerance: Mapped[float] = mapped_column(
        nullable=False,
        default=0.005,
        server_default="0.005",
    )

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
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

    @property
    def cycle_started_at(self) -> datetime:
        return self.created_at

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CompensationCycle fy={self.fy_label!r} status={self.status!r}>"
