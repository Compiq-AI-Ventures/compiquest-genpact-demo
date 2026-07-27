"""Persistence helpers for :class:`CompensationCycle`.

Tenant-scoped: every query carries an explicit ``tenant_id`` filter
even though RLS would catch a missing one.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compensation_cycle import (
    CompensationCycle,
    CompensationCycleStatus,
)


async def get_active(
    db: AsyncSession, tenant_id: uuid.UUID
) -> CompensationCycle | None:
    """Return the (single) active cycle for this tenant, or ``None``."""
    stmt = (
        select(CompensationCycle)
        .where(
            CompensationCycle.tenant_id == tenant_id,
            CompensationCycle.status == CompensationCycleStatus.ACTIVE.value,
        )
        .order_by(CompensationCycle.created_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_for_tenant(
    db: AsyncSession, tenant_id: uuid.UUID, cycle_id: uuid.UUID
) -> CompensationCycle | None:
    """Return one cycle by id, scoped to a tenant."""
    stmt = select(CompensationCycle).where(
        CompensationCycle.id == cycle_id,
        CompensationCycle.tenant_id == tenant_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()
