"""Drop the dead ai_chat tables.

``ai_conversations``, ``ai_messages``, and ``ai_telemetry`` were created
in f8a9b0c1d2e3 for the iQuest AI Chat agent's conversation history and
telemetry, but no ORM model was ever added for them and no repository
or service writes to them — ``/ai/iquest-query`` and
``/ai/suggested-questions`` are fully stateless. Confirmed via a repo-
wide grep for the table names and their would-be model classes: zero
references outside the creating migration. Dropping to simplify the
schema; re-add via a fresh migration if conversation persistence gets
built.

Revision ID: a5b6c7d8e9f0
Revises: e2f3a4b5c6d7
Create Date: 2026-07-14 10:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "a5b6c7d8e9f0"
down_revision: str | Sequence[str] | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("ai_telemetry")
    op.drop_table("ai_messages")
    op.drop_table("ai_conversations")


def downgrade() -> None:
    op.create_table(
        "ai_conversations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cycle_id",
            UUID(as_uuid=True),
            sa.ForeignKey("compensation_cycles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("entity_id", sa.String(255), nullable=True),
        sa.Column("entity_name", sa.String(500), nullable=True),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("msg_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_ai_conversations_tenant_id", "ai_conversations", ["tenant_id"])
    op.create_index("ix_ai_conversations_user_id", "ai_conversations", ["user_id"])
    op.create_index(
        "uq_ai_conversations_active_scope",
        "ai_conversations",
        ["tenant_id", "user_id", "scope", "entity_id", "cycle_id"],
        unique=False,
    )

    op.create_table(
        "ai_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ai_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_calls", sa.JSON(), nullable=True),
        sa.Column("tool_results", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_ai_messages_conv_seq"),
    )
    op.create_index("ix_ai_messages_conversation_id", "ai_messages", ["conversation_id"])

    op.create_table(
        "ai_telemetry",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("request_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ai_conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(20), nullable=True),
        sa.Column("entity_id", sa.String(255), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("provider", sa.String(20), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("tool_calls_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_names", sa.JSON(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 8), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_ai_telemetry_tenant_id", "ai_telemetry", ["tenant_id"])
    op.create_index("ix_ai_telemetry_conversation_id", "ai_telemetry", ["conversation_id"])
