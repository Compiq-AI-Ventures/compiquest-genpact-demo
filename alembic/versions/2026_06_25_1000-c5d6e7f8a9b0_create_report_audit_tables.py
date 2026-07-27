"""create report audit tables

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-06-25 10:00:00.000000

Six tables for the Phase 1 report audit foundation:
  report_runs        — one row per PDF generation request
  report_steps       — step-level timing and status
  report_datasets    — dataset provenance fingerprints
  report_metrics     — computed KPI instances
  report_validations — per-KPI unit and cross-checks
  report_manifest    — immutable PDF passport (1:1 with report_runs)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c5d6e7f8a9b0"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="PENDING"),
        sa.Column("fiscal_year", sa.Integer(), nullable=True),
        sa.Column("scope_label", sa.String(length=128), nullable=True),
        sa.Column("headcount", sa.Integer(), nullable=True),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("report_hash", sa.String(length=64), nullable=True),
        sa.Column("pdf_size_bytes", sa.Integer(), nullable=True),
        sa.Column("template_version", sa.String(length=16), nullable=False, server_default="1.0.0"),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cycle_id"], ["compensation_cycles.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_report_runs_trace_id", "report_runs", ["trace_id"])
    op.create_index("ix_report_runs_tenant_id", "report_runs", ["tenant_id"])

    op.create_table(
        "report_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("step_name", sa.String(length=64), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["report_runs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_report_steps_trace_id", "report_steps", ["trace_id"])
    op.create_index("ix_report_steps_run_id", "report_steps", ["run_id"])
    op.create_index("ix_report_steps_step_name", "report_steps", ["step_name"])

    op.create_table(
        "report_datasets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("source_table", sa.String(length=64), nullable=False),
        sa.Column("query_filter", sa.Text(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=True),
        sa.Column("snapshot_time", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("sample_hash", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["report_runs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_report_datasets_trace_id", "report_datasets", ["trace_id"])
    op.create_index("ix_report_datasets_run_id", "report_datasets", ["run_id"])
    op.create_index("ix_report_datasets_source_table", "report_datasets", ["source_table"])

    op.create_table(
        "report_metrics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("metric_id", sa.String(length=64), nullable=False),
        sa.Column("metric_name", sa.String(length=128), nullable=False),
        sa.Column("metric_value", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("metric_value_str", sa.String(length=64), nullable=True),
        sa.Column("formula", sa.Text(), nullable=True),
        sa.Column("source_dataset", sa.String(length=64), nullable=True),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("section", sa.String(length=32), nullable=False),
        sa.Column("computed_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["report_runs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_report_metrics_trace_id", "report_metrics", ["trace_id"])
    op.create_index("ix_report_metrics_run_id", "report_metrics", ["run_id"])
    op.create_index("ix_report_metrics_metric_id", "report_metrics", ["metric_id"])

    op.create_table(
        "report_validations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("metric_id", sa.String(length=64), nullable=False),
        sa.Column("expected_value", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("actual_value", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("tolerance", sa.Numeric(precision=8, scale=6), nullable=False, server_default="0.001"),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("validated_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["report_runs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_report_validations_trace_id", "report_validations", ["trace_id"])
    op.create_index("ix_report_validations_run_id", "report_validations", ["run_id"])
    op.create_index("ix_report_validations_status", "report_validations", ["status"])

    op.create_table(
        "report_manifest",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("template_version", sa.String(length=16), nullable=False),
        sa.Column("report_version", sa.String(length=16), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("report_hash", sa.String(length=64), nullable=False),
        sa.Column("sections_included", sa.ARRAY(sa.String()), nullable=False),
        sa.Column("sections_withheld", sa.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("metric_count", sa.Integer(), nullable=False),
        sa.Column("validation_pass_count", sa.Integer(), nullable=False),
        sa.Column("validation_fail_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_id"),
        sa.ForeignKeyConstraint(["report_id"], ["report_runs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_report_manifest_trace_id", "report_manifest", ["trace_id"])


def downgrade() -> None:
    op.drop_table("report_manifest")
    op.drop_table("report_validations")
    op.drop_table("report_metrics")
    op.drop_table("report_datasets")
    op.drop_table("report_steps")
    op.drop_table("report_runs")
