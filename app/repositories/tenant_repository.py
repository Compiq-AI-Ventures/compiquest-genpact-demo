"""Persistence helpers for the :class:`~app.models.tenant.Tenant` aggregate."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant


async def get_by_id(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant | None:
    """Return the tenant with the given primary key, or ``None``."""
    return await db.get(Tenant, tenant_id)


async def get_by_code(db: AsyncSession, code: str) -> Tenant | None:
    """Return the tenant with the given code, or ``None``."""
    stmt = select(Tenant).where(Tenant.code == code)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_domain(db: AsyncSession, domain: str) -> Tenant | None:
    """Return the tenant whose ``domain`` matches, or ``None``.

    Used by the login flow to resolve which tenant an in-domain email
    address belongs to (``alice@acme.com`` → tenant where
    ``domain = 'acme.com'``). Domain is unique at the DB level, so
    this is a 0-or-1 lookup.
    """
    stmt = select(Tenant).where(Tenant.domain == domain)
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_tenants(
    db: AsyncSession,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Tenant], int]:
    """Return a page of tenants plus the total matching count.

    Args:
        status: optional filter — only tenants in this status are returned.
        limit / offset: pagination. Caller decides defaults; this is a
            simple offset-based scheme suitable for an admin list view
            (the table is always small relative to the data inside).
    """
    base = select(Tenant)
    if status is not None:
        base = base.where(Tenant.status == status)

    # Count using a separate query — Postgres handles this well and the
    # admin tenants table is small.
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    count_stmt = sa_select(func.count()).select_from(Tenant)
    if status is not None:
        count_stmt = count_stmt.where(Tenant.status == status)
    total = (await db.execute(count_stmt)).scalar_one()

    page_stmt = base.order_by(Tenant.code).limit(limit).offset(offset)
    rows = (await db.execute(page_stmt)).scalars().all()
    return list(rows), total
