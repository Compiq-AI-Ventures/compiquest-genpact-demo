"""JvreRationale ORM model.

Persisted AI-generated compensation rationale for a (cycle, subject) pair.
Written on first generation (streaming or batch seed); subsequent reads
return the stored text directly, skipping the Bedrock call.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class JvreRationale(Base):
    """Persisted AI rationale for one subject in one cycle."""

    __tablename__ = "jvre_rationale"
    __table_args__ = (
        UniqueConstraint(
            "cycle_id",
            "subject_user_id",
            name="uq_jvre_rationale_cycle_subject",
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

    rationale_text: Mapped[str] = mapped_column(Text, nullable=False)

    # Which model produced this text — useful for cache-busting if the
    # model changes and old rationale should be regenerated.
    model_id: Mapped[str] = mapped_column(String(128), nullable=False, default="seeded")

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
