"""Persistence helpers for :class:`CompensationHistory`."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compensation_history import CompensationHistory


async def list_for_subject(
    db: AsyncSession, tenant_id: uuid.UUID, subject_user_id: uuid.UUID
) -> list[CompensationHistory]:
    """Return history rows for a subject, newest FY first."""
    stmt = (
        select(CompensationHistory)
        .where(
            CompensationHistory.tenant_id == tenant_id,
            CompensationHistory.subject_user_id == subject_user_id,
        )
        .order_by(CompensationHistory.fy_label.desc())
    )
    return list((await db.execute(stmt)).scalars().all())
