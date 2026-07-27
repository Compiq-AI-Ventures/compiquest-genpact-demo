"""add_jvre_rationale_table

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-06-10 14:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this migration."""
    op.create_table(
        "jvre_rationale",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), sa.ForeignKey("compensation_cycles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rationale_text", sa.Text(), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False, server_default="seeded"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cycle_id", "subject_user_id", name="uq_jvre_rationale_cycle_subject"),
    )
    op.create_index("ix_jvre_rationale_tenant_id", "jvre_rationale", ["tenant_id"])
    op.create_index("ix_jvre_rationale_cycle_id", "jvre_rationale", ["cycle_id"])
    op.create_index("ix_jvre_rationale_subject_user_id", "jvre_rationale", ["subject_user_id"])


def downgrade() -> None:
    """Revert this migration."""
    op.drop_index("ix_jvre_rationale_subject_user_id", table_name="jvre_rationale")
    op.drop_index("ix_jvre_rationale_cycle_id", table_name="jvre_rationale")
    op.drop_index("ix_jvre_rationale_tenant_id", table_name="jvre_rationale")
    op.drop_table("jvre_rationale")
