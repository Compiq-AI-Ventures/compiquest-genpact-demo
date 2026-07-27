"""Persistence helpers for :class:`MarketBenchmark`."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market_benchmark import MarketBenchmark


async def get_for_subject(
    db: AsyncSession, tenant_id: uuid.UUID, subject_user_id: uuid.UUID
) -> MarketBenchmark | None:
    """Return the (one) benchmark for a subject in this tenant."""
    stmt = select(MarketBenchmark).where(
        MarketBenchmark.tenant_id == tenant_id,
        MarketBenchmark.subject_user_id == subject_user_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()

async def get_for_subjects(
    db: AsyncSession, tenant_id: uuid.UUID, subject_user_ids: list[uuid.UUID]
) -> list[MarketBenchmark]:
    """Return benchmarks for multiple subjects in bulk."""
    if not subject_user_ids:
        return []
    stmt = select(MarketBenchmark).where(
        MarketBenchmark.tenant_id == tenant_id,
        MarketBenchmark.subject_user_id.in_(subject_user_ids),
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())
