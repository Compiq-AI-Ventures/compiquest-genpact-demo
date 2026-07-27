"""Persistence helpers for the :class:`~app.models.user.User` aggregate.

These functions intentionally do not commit. Callers (services) own the
transaction boundary so multiple repository calls can be composed inside a
single unit of work.

Email lookups are tenant-scoped
-------------------------------
Email is unique *per tenant* (``UNIQUE (tenant_id, email) NULLS NOT
DISTINCT``), so a bare email is no longer enough to identify a user.
Lookups must specify the scope:

* :func:`get_platform_user_by_email` — scopes to platform users
  (``tenant_id IS NULL``).
* :func:`get_tenant_user_by_email`   — scopes to a specific tenant.
* :func:`email_exists_anywhere`      — duplicate-detection helper used
  before insert to avoid IntegrityError stack traces in the common
  case.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import User


def _normalize_email(email: str) -> str:
    return email.strip().lower()


async def get_platform_user_by_email(
    db: AsyncSession, email: str
) -> User | None:
    """Return the platform user (``tenant_id IS NULL``) with this email."""
    stmt = select(User).where(
        User.email == _normalize_email(email),
        User.tenant_id.is_(None),
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_tenant_user_by_email(
    db: AsyncSession, tenant_id: uuid.UUID, email: str
) -> User | None:
    """Return the tenant user with this email inside ``tenant_id``."""
    stmt = select(User).where(
        User.email == _normalize_email(email),
        User.tenant_id == tenant_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def email_exists_for_tenant(
    db: AsyncSession, tenant_id: uuid.UUID | None, email: str
) -> bool:
    """True iff a user with this email exists in the given scope.

    Pass ``tenant_id=None`` to check the platform scope. Used as a
    cheap pre-check before insert so the 99% case yields a clean
    domain error instead of an IntegrityError caught later.
    """
    normalized = _normalize_email(email)
    stmt = select(User.id).where(User.email == normalized)
    stmt = stmt.where(
        User.tenant_id.is_(None) if tenant_id is None else User.tenant_id == tenant_id
    )
    result = await db.execute(stmt.limit(1))
    return result.first() is not None


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    """Return the user with the given primary key, or ``None`` if not found."""
    stmt = select(User).where(User.id == user_id).options(selectinload(User.department))
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_id_tenant_scoped(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    load_department: bool = False,
) -> User | None:
    """Return one user scoped to a tenant, or None if not found or cross-tenant."""
    stmt = select(User).where(User.id == user_id, User.tenant_id == tenant_id)
    if load_department:
        stmt = stmt.options(selectinload(User.department))
    return (await db.execute(stmt)).scalar_one_or_none()


async def batch_by_ids_tenant_scoped(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user_ids: list[uuid.UUID],
    *,
    load_department: bool = False,
) -> dict[uuid.UUID, User]:
    """Single query for many users, always scoped to tenant.

    Returns a dict keyed by user_id. Missing users are absent — callers
    should use ``.get(uid)`` rather than ``[uid]``.
    """
    if not user_ids:
        return {}
    stmt = select(User).where(User.id.in_(user_ids), User.tenant_id == tenant_id)
    if load_department:
        stmt = stmt.options(selectinload(User.department))
    rows = (await db.execute(stmt)).scalars().all()
    return {u.id: u for u in rows}


async def create_user(db: AsyncSession, user: User) -> User:
    """Add ``user`` to the session and flush so server-side defaults populate.

    The session is *not* committed here — the request's Unit of Work
    (``get_db``) commits once at the end.
    """
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user
