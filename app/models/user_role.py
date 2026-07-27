"""``user_roles`` association table — User ↔ Role grants.

Single-tenant-per-user model
----------------------------
A row says "this user holds this role." The tenant the grant applies
in is implicit: it's whatever tenant the user belongs to (or the
platform tier if ``users.tenant_id IS NULL``).

Scope-vs-tenant consistency is enforced by the service layer
(``admin_user_service``) — platform users may only be granted
PLATFORM-scope roles; tenant users may only be granted TENANT-scope
roles. We deliberately do not enforce this with a DB check constraint
because the constraint would have to span tables (``users.tenant_id``
+ ``roles.scope``); the service-layer check is the single source of
truth.

Uniqueness: a user cannot hold the same role twice.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserRole(Base):
    """One user-role grant."""

    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "role_id",
            name="uq_user_roles_user_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UserRole user_id={self.user_id} role_id={self.role_id}>"
