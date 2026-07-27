"""Tenant ORM model — one row per customer organization.

A tenant is the top-level isolation boundary for everything that isn't
platform-wide. Users belong to exactly one tenant, captured by
:attr:`~app.models.user.User.tenant_id` (nullable: NULL means platform
user).

The ``domain`` column is the canonical identifier for email-routing
and SSO discovery. It is required, syntactically validated by the
schema layer, and globally unique — two different tenants cannot
share the same email/SSO domain.

Status semantics
----------------
* ``ACTIVE``    — normal operations.
* ``SUSPENDED`` — read-only / login-disabled, billable. Restorable.
* ``DISABLED``  — terminated. Audit trail and data retained per policy.

The status is enforced by application code, not by a DB constraint, so
we can add new states without a migration.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, Uuid, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class TenantStatus(enum.StrEnum):
    """Allowed values for :attr:`Tenant.status`."""

    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DISABLED = "DISABLED"


class Tenant(Base):
    """One customer / workspace boundary."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Stable, machine-friendly identifier (e.g. ``"acme"``). Used in
    # subdomains, log lines, audit rows. Treat as effectively immutable.
    code: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )

    # Customer's canonical email/SSO domain (e.g. ``acme.com``).
    # Required and globally unique — used to resolve which tenant an
    # in-domain user belongs to at login time, and as the discovery
    # anchor for future SSO. Lowercased + regex-validated by the
    # schema layer; the DB enforces uniqueness.
    domain: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=TenantStatus.ACTIVE.value,
        server_default=TenantStatus.ACTIVE.value,
    )

    # Default currency for monetary columns scoped to this tenant.
    # ISO 4217 (e.g. "USD", "INR"). Every monetary column on every
    # tenant-scoped table carries its own currency_code so multi-
    # currency support is a data fact, not a migration. There is no UI
    # switcher in v0.1 — the default is what gets stored everywhere.
    default_currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="USD",
        server_default="USD",
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

    # Reverse side of User.tenant — every user whose tenant_id points
    # at this row.
    users: Mapped[list[User]] = relationship(
        "User",
        back_populates="tenant",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Tenant code={self.code!r} status={self.status!r}>"
