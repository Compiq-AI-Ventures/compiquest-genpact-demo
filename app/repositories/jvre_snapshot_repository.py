"""Persistence helpers for :class:`JvreSnapshot`."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.jvre_snapshot import JvreSnapshot
from app.models.reporting_relationship import ReportingRelationship


async def get_for_subject(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    subject_user_id: uuid.UUID,
) -> JvreSnapshot | None:
    """Return the JVRE snapshot for one (cycle, subject) pair."""
    stmt = select(JvreSnapshot).where(
        JvreSnapshot.tenant_id == tenant_id,
        JvreSnapshot.cycle_id == cycle_id,
        JvreSnapshot.subject_user_id == subject_user_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_for_subjects(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    subject_user_ids: list[uuid.UUID],
) -> list[JvreSnapshot]:
    """Bulk fetch — used by the recommendations list endpoint to
    avoid N+1 queries when rendering chips for a whole team."""
    if not subject_user_ids:
        return []
    stmt = select(JvreSnapshot).where(
        JvreSnapshot.tenant_id == tenant_id,
        JvreSnapshot.cycle_id == cycle_id,
        JvreSnapshot.subject_user_id.in_(subject_user_ids),
    )
    return list((await db.execute(stmt)).scalars().all())


async def current_pool_by_manager(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    manager_ids: list[uuid.UUID],
) -> dict[uuid.UUID, Decimal]:
    """Sum current_base + current_variable across each manager's direct reports.

    Single aggregated query — no N+1. Managers with no snapshot-covered
    reports are absent from the result (caller treats missing as None).
    """
    if not manager_ids:
        return {}
    stmt = (
        select(
            ReportingRelationship.manager_user_id,
            func.sum(
                func.coalesce(JvreSnapshot.current_base, 0)
                + func.coalesce(JvreSnapshot.current_variable, 0)
            ).label("current_pool"),
        )
        .join(
            JvreSnapshot,
            and_(
                JvreSnapshot.subject_user_id == ReportingRelationship.report_user_id,
                JvreSnapshot.cycle_id == ReportingRelationship.cycle_id,
                JvreSnapshot.tenant_id == ReportingRelationship.tenant_id,
            ),
        )
        .where(
            ReportingRelationship.manager_user_id.in_(manager_ids),
            ReportingRelationship.cycle_id == cycle_id,
            ReportingRelationship.tenant_id == tenant_id,
        )
        .group_by(ReportingRelationship.manager_user_id)
    )
    rows = (await db.execute(stmt)).all()
    return {row.manager_user_id: row.current_pool for row in rows}
