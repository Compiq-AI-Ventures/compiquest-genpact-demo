"""Persistence helpers for the :class:`~app.models.role.Role` aggregate."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.role import Role


async def get_by_code(db: AsyncSession, code: str) -> Role | None:
    """Return the role with the given code, or ``None`` if not found."""
    stmt = select(Role).where(Role.code == code)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_active(db: AsyncSession) -> list[Role]:
    """Return every currently-active role, ordered by code."""
    stmt = select(Role).where(Role.is_active.is_(True)).order_by(Role.code)
    result = await db.execute(stmt)
    return list(result.scalars().all())
