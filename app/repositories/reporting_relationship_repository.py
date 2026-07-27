"""Persistence helpers for :class:`ReportingRelationship`.

The org chart is captured per cycle so re-orgs mid-cycle don't rewrite
history. Two queries cover most needs:

* :func:`list_reports` — direct reports of a manager in a cycle.
* :func:`get_manager_of` — the manager of one report in a cycle.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reporting_relationship import ReportingRelationship


async def list_reports(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    manager_user_id: uuid.UUID,
) -> list[ReportingRelationship]:
    """Return the rows for a manager's direct reports in this cycle."""
    stmt = (
        select(ReportingRelationship)
        .where(
            ReportingRelationship.tenant_id == tenant_id,
            ReportingRelationship.cycle_id == cycle_id,
            ReportingRelationship.manager_user_id == manager_user_id,
        )
        .order_by(ReportingRelationship.report_user_id)
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_manager_of(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    report_user_id: uuid.UUID,
) -> ReportingRelationship | None:
    """Return the (single) row giving this report's manager in this cycle.

    The unique constraint ``(cycle_id, report_user_id)`` guarantees at
    most one. Returns ``None`` for a user with no manager (e.g. CFO).
    """
    stmt = select(ReportingRelationship).where(
        ReportingRelationship.tenant_id == tenant_id,
        ReportingRelationship.cycle_id == cycle_id,
        ReportingRelationship.report_user_id == report_user_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def report_ids(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    manager_user_id: uuid.UUID,
) -> list[uuid.UUID]:
    """Convenience: just the report_user_id list."""
    rows = await list_reports(db, tenant_id, cycle_id, manager_user_id)
    return [r.report_user_id for r in rows]


async def report_ids_by_manager(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    manager_user_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """Bulk variant of :func:`report_ids` — one query for many managers.

    Returns ``{manager_user_id: [report_user_id, ...]}``. Managers with
    no reports in this cycle are absent from the dict (callers should
    use ``dict.get(mgr_id, [])``). Used by aggregation endpoints (e.g.
    Team Risk Snapshot) that need the org tree for a whole MoM's
    sub-tree in one round-trip — replaces an N+1 over :func:`report_ids`.
    """
    if not manager_user_ids:
        return {}
    stmt = (
        select(ReportingRelationship)
        .where(
            ReportingRelationship.tenant_id == tenant_id,
            ReportingRelationship.cycle_id == cycle_id,
            ReportingRelationship.manager_user_id.in_(manager_user_ids),
        )
        .order_by(
            ReportingRelationship.manager_user_id,
            ReportingRelationship.report_user_id,
        )
    )
    rows = (await db.execute(stmt)).scalars().all()
    out: dict[uuid.UUID, list[uuid.UUID]] = {}
    for r in rows:
        out.setdefault(r.manager_user_id, []).append(r.report_user_id)
    return out
