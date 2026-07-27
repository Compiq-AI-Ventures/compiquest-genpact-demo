"""ORM models for the Phase 2.5 agentic pipeline audit tables.

Three tables:
  agent_pipeline_runs  — one row per report PDF generation (top-level run)
  agent_run_logs       — one row per agent execution within a pipeline run
  tool_run_logs        — one row per tool call within an agent execution

PKs are supplied from AgentRuntime (not DB-generated) so in-memory IDs
match DB IDs exactly. This lets the repository flush without extra SELECTs.

``run_id`` on :class:`AgentPipelineRun` is a plain correlation UUID
minted per report request — there is no ``report_runs`` table; the
compensation report PDF is generated and streamed back without a
persisted per-run audit trail.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AgentPipelineRun(Base):
    __tablename__ = "agent_pipeline_runs"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    trace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(16), nullable=False, default="2.5.0")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="COMPLETED")
    agent_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    tool_call_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    total_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class AgentRunLog(Base):
    __tablename__ = "agent_run_logs"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_pipeline_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    trace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    input_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class ToolRunLog(Base):
    __tablename__ = "tool_run_logs"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_pipeline_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_run_logs.id", ondelete="CASCADE"),
        nullable=False,
    )
    trace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    input_repr: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_repr: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
