"""Role ORM model — rows in the ``roles`` table.

A role is a named, code-addressable record. The application ships
with a built-in set (see :data:`app.core.roles.DEFAULT_ROLES`) and
new roles can be added at runtime by inserting rows.

Authorization checks reference roles by their ``code`` (a stable,
machine-friendly identifier). ``name`` and ``description`` are
human-facing labels that can be edited freely without affecting any
authorization decision.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, Uuid, func, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.roles import RoleScope

if TYPE_CHECKING:
    from app.models.user import User


class Role(Base):
    """A role a user can hold."""

    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )

    # Stable identifier used by authorization code (``require_roles``,
    # JWT claims, ``user.roles``). Editing this would break every place
    # that references the role — treat as effectively immutable.
    code: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )

    # Human-readable label; safe to edit at any time.
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Authorization scope. PLATFORM roles ignore tenant context; TENANT
    # roles are only meaningful inside a specific tenant. Stored on the
    # role rather than computed from code so custom roles can declare
    # their own scope at insert time.
    scope: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
        default=RoleScope.TENANT.value,
        server_default=RoleScope.TENANT.value,
    )

    # Distinguishes built-in roles (shipped via the DEFAULT_ROLES seed)
    # from runtime-created custom roles. Useful for admin UIs that
    # forbid editing the built-in set.
    is_system_role: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Reverse side of User.roles. ``viewonly=True`` because writes are
    # done through ``user.roles.append(...)`` from the User side; SA
    # warns otherwise that the M2M back-population is ambiguous.
    users: Mapped[list[User]] = relationship(
        "User",
        secondary="user_roles",
        back_populates="roles",
        viewonly=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Role code={self.code!r} name={self.name!r}>"
