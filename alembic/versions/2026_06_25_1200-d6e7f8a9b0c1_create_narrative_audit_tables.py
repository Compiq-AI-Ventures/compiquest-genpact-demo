"""create narrative audit tables

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-06-25 12:00:00.000000

Four tables for the Phase 2 narrative audit foundation:
  narrative_generations — one row per Bedrock invocation (max 2 per run)
  report_claims         — one row per extracted factual claim
  claim_verifications   — one row per claim verification result
  faithfulness_results  — aggregate faithfulness score per attempt
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "narrative_generations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("contract_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("narrative_status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("context_payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("raw_output", sa.Text(), nullable=True),
        sa.Column("parsed_sections", postgresql.JSONB(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["report_runs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_ng_trace_id", "narrative_generations", ["trace_id"])
    op.create_index("ix_ng_run_id", "narrative_generations", ["run_id"])
    op.create_index("ix_ng_status", "narrative_generations", ["status"])

    op.create_table(
        "report_claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("section", sa.String(length=64), nullable=False),
        sa.Column("claim_type", sa.String(length=16), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("metric_id", sa.String(length=64), nullable=True),
        sa.Column("char_offset", sa.Integer(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["report_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["generation_id"], ["narrative_generations.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_rc_trace_id", "report_claims", ["trace_id"])
    op.create_index("ix_rc_run_id", "report_claims", ["run_id"])
    op.create_index("ix_rc_generation_id", "report_claims", ["generation_id"])
    op.create_index("ix_rc_claim_type", "report_claims", ["claim_type"])

    op.create_table(
        "claim_verifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("matched_metric", sa.String(length=64), nullable=True),
        sa.Column("registry_value", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("claimed_value", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("delta", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("verified_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["report_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["claim_id"], ["report_claims.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_cv_trace_id", "claim_verifications", ["trace_id"])
    op.create_index("ix_cv_run_id", "claim_verifications", ["run_id"])
    op.create_index("ix_cv_claim_id", "claim_verifications", ["claim_id"])
    op.create_index("ix_cv_status", "claim_verifications", ["status"])

    op.create_table(
        "faithfulness_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("claims_total", sa.Integer(), nullable=False),
        sa.Column("claims_pass", sa.Integer(), nullable=False),
        sa.Column("claims_fail", sa.Integer(), nullable=False),
        sa.Column("claims_skipped", sa.Integer(), nullable=False),
        sa.Column("faithfulness_score", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("failed_claim_texts", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("orphan_metric_ids", sa.ARRAY(sa.String(64)), nullable=True),
        sa.Column("model_id", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["report_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["generation_id"], ["narrative_generations.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_fr_trace_id", "faithfulness_results", ["trace_id"])
    op.create_index("ix_fr_run_id", "faithfulness_results", ["run_id"])
    op.create_index("ix_fr_decision", "faithfulness_results", ["decision"])
    op.create_index("ix_fr_score", "faithfulness_results", ["faithfulness_score"])


def downgrade() -> None:
    op.drop_table("faithfulness_results")
    op.drop_table("claim_verifications")
    op.drop_table("report_claims")
    op.drop_table("narrative_generations")
