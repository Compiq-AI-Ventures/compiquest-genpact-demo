"""create agent audit tables

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-06-25 14:00:00.000000

Three tables for the Phase 2.5 agentic pipeline audit:
  agent_pipeline_runs  — one per report PDF generation
  agent_run_logs       — one per agent execution
  tool_run_logs        — one per tool call within an agent execution
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e7f8a9b0c1d2"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # agent_pipeline_runs
    op.create_table(
        "agent_pipeline_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("report_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pipeline_version", sa.String(16), nullable=False, server_default="2.5.0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="COMPLETED"),
        sa.Column("agent_count", sa.SmallInteger(), nullable=True),
        sa.Column("tool_call_count", sa.SmallInteger(), nullable=True),
        sa.Column("total_duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_agent_pipeline_runs_trace_id", "agent_pipeline_runs", ["trace_id"])

    # agent_run_logs
    op.create_table(
        "agent_run_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "pipeline_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_pipeline_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.String(64), nullable=False),
        sa.Column("execution_order", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("input_summary", sa.Text(), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_agent_run_logs_trace_id", "agent_run_logs", ["trace_id"])
    op.create_index("ix_agent_run_logs_pipeline_run_id", "agent_run_logs", ["pipeline_run_id"])

    # tool_run_logs
    op.create_table(
        "tool_run_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "pipeline_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_pipeline_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_run_logs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("input_repr", sa.Text(), nullable=True),
        sa.Column("output_repr", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_tool_run_logs_trace_id", "tool_run_logs", ["trace_id"])
    op.create_index("ix_tool_run_logs_agent_run_id", "tool_run_logs", ["agent_run_id"])


def downgrade() -> None:
    op.drop_table("tool_run_logs")
    op.drop_table("agent_run_logs")
    op.drop_table("agent_pipeline_runs")
