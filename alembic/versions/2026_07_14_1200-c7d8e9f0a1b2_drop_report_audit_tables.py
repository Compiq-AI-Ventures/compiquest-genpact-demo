"""Drop the report-generation audit-trail tables.

The compensation report PDF (``GET /ai/reports/compensation.pdf``) is
now generated and streamed straight back to the caller — no per-run
audit trail is persisted. Drops:

  report_runs         — one row per report generation request
  report_steps        — one row per logical step within a run
  report_datasets     — provenance for each data source used
  report_metrics      — every computed KPI with formula and source
  report_validations  — computed-vs-render comparison for each KPI
  report_manifest     — immutable "passport" of the finished PDF artifact

``narrative_generations.run_id`` and ``agent_pipeline_runs.run_id`` both
FK'd to ``report_runs.id`` — those tables stay (Phase 2 / 2.5 pipeline
logging is still persisted), but the FK constraints are dropped first
and the columns become plain correlation UUIDs, since there is no
run table left for them to reference.

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-07-14 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: str | Sequence[str] | None = "b6c7d8e9f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the two FKs into report_runs before the table goes away.
    op.drop_constraint(
        "narrative_generations_run_id_fkey", "narrative_generations", type_="foreignkey"
    )
    op.drop_constraint(
        "agent_pipeline_runs_run_id_fkey", "agent_pipeline_runs", type_="foreignkey"
    )

    op.drop_table("report_manifest")
    op.drop_table("report_validations")
    op.drop_table("report_metrics")
    op.drop_table("report_datasets")
    op.drop_table("report_steps")
    op.drop_table("report_runs")


def downgrade() -> None:
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
        sa.ForeignKeyConstraint(["report_id"], ["report_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("report_id"),
    )
    op.create_index("ix_report_manifest_trace_id", "report_manifest", ["trace_id"])

    op.create_foreign_key(
        "narrative_generations_run_id_fkey",
        "narrative_generations",
        "report_runs",
        ["run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "agent_pipeline_runs_run_id_fkey",
        "agent_pipeline_runs",
        "report_runs",
        ["run_id"],
        ["id"],
        ondelete="CASCADE",
    )
