"""Persistence helpers for the :class:`~app.models.department.Department`
aggregate.

Every query is *explicitly* scoped to a ``tenant_id`` even though
RLS would catch a missing filter. Two layers of defense keep the
enterprise-grade promise that one tenant can never see another's
rows even if the GUC plumbing breaks.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department


async def list_for_tenant(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Department], int]:
    """Return ``(rows, total)`` for a tenant's departments."""
    from sqlalchemy import func

    rows_stmt = (
        select(Department)
        .where(Department.tenant_id == tenant_id)
        .order_by(Department.code)
        .limit(limit)
        .offset(offset)
    )
    count_stmt = (
        select(func.count())
        .select_from(Department)
        .where(Department.tenant_id == tenant_id)
    )

    rows = (await db.execute(rows_stmt)).scalars().all()
    total = (await db.execute(count_stmt)).scalar_one()
    return list(rows), total


async def get_for_tenant(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID,
) -> Department | None:
    """Return one department or ``None`` if it doesn't exist *in this tenant*.

    Note the tenant filter — even if RLS lets a row through (e.g.,
    in a misconfigured environment), this returns ``None`` for rows
    that don't belong to ``tenant_id``.
    """
    stmt = select(Department).where(
        Department.id == department_id, Department.tenant_id == tenant_id
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_code(
    db: AsyncSession, tenant_id: uuid.UUID, code: str
) -> Department | None:
    """Return one department by its (tenant-scoped) code, or ``None``."""
    stmt = select(Department).where(
        Department.tenant_id == tenant_id, Department.code == code
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def create(db: AsyncSession, department: Department) -> Department:
    """Add ``department`` to the session and flush. Caller commits."""
    db.add(department)
    await db.flush()
    await db.refresh(department)
    return department


async def delete(db: AsyncSession, department: Department) -> None:
    """Mark ``department`` for deletion. Caller commits."""
    await db.delete(department)
    await db.flush()
