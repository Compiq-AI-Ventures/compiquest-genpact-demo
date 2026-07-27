"""User ORM model.

Single-tenant-per-user model
----------------------------
Every user belongs to exactly one tenant, OR to no tenant at all
(platform users — SUPER_ADMIN, PLATFORM_ADMIN, SUPPORT_ADMIN). The
membership is captured by a non-null vs null ``tenant_id`` on this
table:

* ``tenant_id IS NULL`` → platform user. Holds only PLATFORM-scope
  role grants. Operates above any individual tenant.
* ``tenant_id IS NOT NULL`` → tenant user. Holds only TENANT-scope
  role grants, all targeting the same tenant.

Email uniqueness is per-tenant, enforced by
``UNIQUE (tenant_id, email) NULLS NOT DISTINCT`` (Postgres 15+). That
lets two different customers each have their own ``alice@hr.com``
without colliding, while still preventing a single tenant (or the
platform tier) from owning the same email twice.

Roles are stored in the ``roles`` table and linked through the
``user_roles`` association. Access them via ``user.roles`` (a list of
:class:`~app.models.role.Role` instances). The relationship uses
``lazy="selectin"`` so roles are loaded transparently whenever a user
is fetched — authorization code can rely on ``user.roles`` being
populated without orchestrating eager-load queries itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint, Uuid, func, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.department import Department
    from app.models.role import Role
    from app.models.tenant import Tenant


class User(Base):
    """Application user — bound to a single tenant or to the platform tier."""

    __tablename__ = "users"
    __table_args__ = (
        # Per-tenant email uniqueness. NULLS NOT DISTINCT (PG 15+) means
        # (NULL, "alice@x.com") inserted twice is also rejected, which
        # gives platform users (tenant_id NULL) global email uniqueness
        # for free.
        UniqueConstraint(
            "tenant_id",
            "email",
            name="uq_users_tenant_email",
            postgresql_nulls_not_distinct=True,
        ),
    )

    # ----- Identity ---------------------------------------------------------
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )

    # ----- Tenant binding ---------------------------------------------------
    # NULL = platform user. NOT NULL = tenant user. The
    # PLATFORM-vs-TENANT scope of every role grant on this user MUST
    # match the tenant_id NULL-ness; that's enforced in the service
    # layer (admin_user_service) rather than via a check constraint
    # because the constraint would have to span tables.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # ----- Credentials ------------------------------------------------------
    email: Mapped[str] = mapped_column(
        String(255),
        index=True,
        nullable=False,
    )
    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # ----- Profile ----------------------------------------------------------
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ----- Status -----------------------------------------------------------
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    # ----- Audit timestamps -------------------------------------------------
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

    # ----- Department ------------------------------------------------------
    department: Mapped[Department | None] = relationship(
        "Department",
        foreign_keys=[department_id],
        lazy="selectin",
    )

    # ----- Roles (many-to-many via user_roles) ------------------------------
    # Append/remove ``Role`` instances on this collection — SA generates
    # the INSERT/DELETE on user_roles automatically. ``created_at`` on
    # the link gets its server default.
    roles: Mapped[list[Role]] = relationship(
        "Role",
        secondary="user_roles",
        back_populates="users",
        # ``selectin`` issues one extra SELECT per query that returns
        # users; predictable, avoids N+1, no extra columns join into
        # the parent SELECT.
        lazy="selectin",
    )

    # ----- Tenant membership (single) ---------------------------------------
    # The single tenant this user belongs to (or None for platform
    # users). Loaded eagerly so authorization code never trips on a
    # detached attribute.
    tenant: Mapped[Tenant | None] = relationship(
        "Tenant",
        back_populates="users",
        lazy="selectin",
    )

    @validates("email")
    def _normalize_email(self, _key: str, value: str) -> str:
        """Belt-and-suspenders: lowercase email no matter where the value
        came from (API request, internal script, test fixture)."""
        return value.strip().lower() if value else value

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} email={self.email!r} tenant_id={self.tenant_id}>"
