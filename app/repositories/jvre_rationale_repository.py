"""Persistence helpers for :class:`JvreRationale`."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.jvre_rationale import JvreRationale


async def upsert(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    subject_user_id: uuid.UUID,
    rationale_text: str,
    model_id: str,
) -> JvreRationale:
    """Insert or update the rationale row for a (cycle, subject) pair."""
    stmt = select(JvreRationale).where(
        JvreRationale.tenant_id == tenant_id,
        JvreRationale.cycle_id == cycle_id,
        JvreRationale.subject_user_id == subject_user_id,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        row = JvreRationale(
            tenant_id=tenant_id,
            cycle_id=cycle_id,
            subject_user_id=subject_user_id,
            rationale_text=rationale_text,
            model_id=model_id,
        )
        db.add(row)
    else:
        row.rationale_text = rationale_text
        row.model_id = model_id
    await db.flush()
    return row
