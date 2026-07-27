"""Persistence helpers for :class:`BudgetAllocation` + lines."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget_allocation import (
    BudgetAllocation,
    BudgetAllocationLine,
)


async def get_for_owner(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    owner_user_id: uuid.UUID,
) -> BudgetAllocation | None:
    """Return the allocation row owned by ``owner_user_id`` in this cycle.

    The unique constraint on ``(cycle_id, owner_user_id)`` guarantees at
    most one row.
    """
    stmt = select(BudgetAllocation).where(
        BudgetAllocation.tenant_id == tenant_id,
        BudgetAllocation.cycle_id == cycle_id,
        BudgetAllocation.owner_user_id == owner_user_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_for_tenant(
    db: AsyncSession, tenant_id: uuid.UUID, allocation_id: uuid.UUID
) -> BudgetAllocation | None:
    """Return one allocation by id, scoped to a tenant."""
    stmt = select(BudgetAllocation).where(
        BudgetAllocation.id == allocation_id,
        BudgetAllocation.tenant_id == tenant_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_lines_for_allocation(
    db: AsyncSession, allocation_id: uuid.UUID
) -> list[BudgetAllocationLine]:
    """Return every line for an allocation, ordered by recipient_user_id.

    Caller is responsible for verifying the allocation belongs to the
    caller's tenant — this repository function trusts the parent
    has already been scoped.
    """
    stmt = (
        select(BudgetAllocationLine)
        .where(BudgetAllocationLine.allocation_id == allocation_id)
        .order_by(BudgetAllocationLine.recipient_user_id)
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_line(
    db: AsyncSession,
    allocation_id: uuid.UUID,
    line_id: uuid.UUID,
) -> BudgetAllocationLine | None:
    """Return one line by id, scoped to its parent allocation."""
    stmt = select(BudgetAllocationLine).where(
        BudgetAllocationLine.id == line_id,
        BudgetAllocationLine.allocation_id == allocation_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_line_for_recipient(
    db: AsyncSession,
    allocation_id: uuid.UUID,
    recipient_user_id: uuid.UUID,
) -> BudgetAllocationLine | None:
    """Return the (single) line for a recipient on a parent allocation."""
    stmt = select(BudgetAllocationLine).where(
        BudgetAllocationLine.allocation_id == allocation_id,
        BudgetAllocationLine.recipient_user_id == recipient_user_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()
