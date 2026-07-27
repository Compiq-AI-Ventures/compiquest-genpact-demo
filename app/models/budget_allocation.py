"""Budget allocation models.

Two tables in one module because they always travel together:

* :class:`BudgetAllocation` — one row per (cycle, owner). The owner is
  the actor whose Budget Planner this represents (CFO, MoM, MoP, …).
  Carries the pool, the reserve, and the cascading lineage via
  ``parent_allocation_id`` (NULL for the CFO's root row; non-NULL for
  every downstream allocation).
* :class:`BudgetAllocationLine` — one row per recipient of a parent
  allocation. The MoM's allocation has one line per direct-report MoP;
  the CFO's allocation has one line per MoM. Carries the per-pool
  split (base / variable / LTI / reserve) so the screen renders without
  joins.

Status semantics on the allocation
----------------------------------
* ``PENDING``   — owner can edit lines + reserve; nothing downstream
  yet.
* ``SUBMITTED`` — owner clicked "Submit Allocation Plan". Each line
  spawns a child allocation in ``PENDING`` for the recipient. The
  parent becomes read-only.
* ``APPROVED``  — the next-tier reviewer signed off. Used in v0.2 when
  C-Suite review enters the picture; for v0.1 ``SUBMITTED`` is
  effectively terminal.
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
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# NUMERIC(18, 2) gives ~999 quadrillion with 2 decimal places — plenty
# of headroom for any tenant-level compensation budget while staying
# cheap in storage.
_MONEY = Numeric(18, 2)


class BudgetAllocationStatus(enum.StrEnum):
    """Allowed values for :attr:`BudgetAllocation.status`."""

    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"


class BudgetAllocation(Base):
    """One actor's budget row for one cycle."""

    __tablename__ = "budget_allocations"
    __table_args__ = (
        # An actor has at most one allocation per cycle. The CFO's
        # root row is unique by (cycle, CFO_user_id); a MoM's row is
        # unique by (cycle, MoM_user_id); etc.
        UniqueConstraint(
            "cycle_id", "owner_user_id", name="uq_budget_allocations_cycle_owner"
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

    # The actor whose planner this is.
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Self-FK: which upstream line funded this allocation. NULL for the
    # CFO's root row.
    parent_allocation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("budget_allocations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # The pool granted to this owner. For the CFO this is the total
    # comp budget; for an MoM it's the slice they were given by the
    # CFO; for an MoP it's the slice they were given by their MoM.
    total_pool: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    strategic_reserve: Mapped[Decimal] = mapped_column(
        _MONEY, nullable=False, default=Decimal("0"), server_default="0"
    )
    # Stored even though it's derivable (total_pool - strategic_reserve)
    # so historical reads don't need to recompute.
    budget_for_allocation: Mapped[Decimal] = mapped_column(
        _MONEY, nullable=False
    )

    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD", server_default="USD"
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=BudgetAllocationStatus.PENDING.value,
        server_default=BudgetAllocationStatus.PENDING.value,
    )

    submitted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    submitted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
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

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<BudgetAllocation owner={self.owner_user_id} "
            f"pool={self.total_pool} status={self.status!r}>"
        )


class BudgetAllocationLine(Base):
    """One recipient's slice of a parent allocation.

    The line carries both the headline ``allocated_amount`` (what shows
    on the MoP's Total Pool when this line submits) AND the per-pool
    split (Base / Variable / LTI / Reserve) so the MoM's screen can
    render without re-joining or re-aggregating. JVRE Rec snapshots
    are captured at line creation time so drift in JVRE doesn't
    rewrite history.
    """

    __tablename__ = "budget_allocation_lines"
    __table_args__ = (
        UniqueConstraint(
            "allocation_id",
            "recipient_user_id",
            name="uq_budget_allocation_lines_allocation_recipient",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    allocation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("budget_allocations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Headline amount this recipient receives (sum of the four pool
    # columns when the MoM is being explicit; or the same number
    # broken down on a card when the MoP receives it).
    allocated_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    base_pool: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    variable_pool: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    lti_grant_fmv_pool: Mapped[Decimal] = mapped_column(
        _MONEY, nullable=False
    )
    reserve_pool: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)

    # JVRE Rec snapshot at line-creation time. Used to render the
    # "JVRE Rec" column on the screen and to compute deviation.
    jvre_rec_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)

    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD", server_default="USD"
    )

    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

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
            f"<BudgetAllocationLine recipient={self.recipient_user_id} "
            f"amount={self.allocated_amount}>"
        )
