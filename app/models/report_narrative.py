"""ORM model for the Phase 2 narrative audit table.

* NarrativeGeneration — one row per Bedrock call (scoped by trace_id and run_id)

The narrative is persisted as-generated; there is no claim extraction,
claim verification, or faithfulness-scoring stage downstream of it.

``run_id`` is a plain correlation UUID minted per report request — there
is no ``report_runs`` table to reference; the compensation report PDF
is generated and streamed back without a persisted per-run audit trail.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Integer,
    SmallInteger,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class NarrativeGeneration(Base):
    """One row per Bedrock invocation (max 2 per report run)."""

    __tablename__ = "narrative_generations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    attempt: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # PENDING | COMPLETED | BLOCKED | FAILED
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    # GENERATED | WITHHELD | BLOCKED
    narrative_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    context_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    raw_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_sections: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
