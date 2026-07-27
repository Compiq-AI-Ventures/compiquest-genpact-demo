"""ReportingRelationship ORM model.

The org chart is captured per cycle (not at the user level) so re-orgs
mid-cycle don't rewrite history. Each row says "for cycle X, user R
reports to manager M". The recommendation engine resolves "who can I
write for" by reading these rows.

Constraints
-----------
* ``UNIQUE (cycle_id, report_user_id)`` — every IC has exactly one
  manager per cycle. Splits in real life are modeled as two cycles.
* Both manager and report are tenant-scoped users; cross-tenant
  reporting is impossible by construction (the FK chain prevents it,
  and RLS would hide rows from the other tenant anyway).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import ForeignKey, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ReportingRelationship(Base):
    """One (cycle, manager → report) edge of the org chart."""

    __tablename__ = "reporting_relationships"
    __table_args__ = (
        UniqueConstraint(
            "cycle_id",
            "report_user_id",
            name="uq_reporting_relationships_cycle_report",
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

    manager_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    report_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    effective_from: Mapped[date | None] = mapped_column(nullable=True)
    effective_to: Mapped[date | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ReportingRelationship cycle={self.cycle_id} "
            f"mgr={self.manager_user_id} report={self.report_user_id}>"
        )
