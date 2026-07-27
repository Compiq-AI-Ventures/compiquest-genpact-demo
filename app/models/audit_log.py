"""AuditLog ORM model — append-only audit trail for sensitive actions.

Every row records a single attempted or completed action: who did it,
what they did, what they did it to, and the request context. Treat the
table as **append-only**: nothing in the codebase should ever UPDATE or
DELETE a row. (Retention / archival policies, when we adopt them, run
out-of-band.)

Design notes
------------
* ``actor_user_id`` is nullable so we can record actions taken by
  unauthenticated callers (e.g. failed login attempts).
* ``resource_id`` is a string (not a UUID) because audit rows can refer
  to many different entity types — UUIDs, role codes (``"HR"``),
  numeric IDs, etc.
* ``metadata`` is JSONB so we can attach action-specific context
  without a schema migration per action type. **Never** put passwords,
  tokens, full request bodies, or sensitive PII into it — see
  :func:`app.services.audit_log_service.log_action` for rules.
* The Python attribute is ``extra_data``; the DB column is ``metadata``.
  SQLAlchemy reserves ``metadata`` as a class attribute on declarative
  bases (it's the ``MetaData`` instance), so we map the column under a
  different Python name.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AuditLog(Base):
    """One audit-trail row."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )

    # ---- Who ------------------------------------------------------------
    # Nullable: unauthenticated actions (failed logins, anonymous probes)
    # still produce audit rows but have no actor.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ---- Tenant scope ---------------------------------------------------
    # Nullable: platform-level actions (super-admin operations, anonymous
    # /auth/login probes) have no tenant. Tenant-scoped actions (anything
    # touching a tenant's data) MUST set this so audit queries can be
    # filtered to a single customer.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ---- What -----------------------------------------------------------
    # Free-form action code (USER_REGISTERED, LOGIN_SUCCESS, ACCESS_DENIED,
    # ...). Not constrained to an enum — new action types should be
    # addable without a migration.
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # ---- What was acted on ---------------------------------------------
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    # ---- Request context ------------------------------------------------
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # IPv6 tops out at 45 characters.
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ---- Action-specific extras ----------------------------------------
    # NOTE: column is ``metadata``; Python attribute is ``extra_data``
    # because ``metadata`` is reserved on SQLAlchemy declarative bases.
    extra_data: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)

    # ---- When -----------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AuditLog id={self.id} action={self.action!r} "
            f"actor_user_id={self.actor_user_id} created_at={self.created_at}>"
        )
