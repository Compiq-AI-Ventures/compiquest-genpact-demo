"""iQuest AI — context-snapshot builders for BUDGET and GLOBAL scopes.

Each function queries the database and returns a plain-text markdown block
that is injected into the LLM prompt. Keeping these here (rather than in the
router) means they can be called from background tasks, CLI commands, or
multiple endpoints without touching FastAPI routing code.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget_allocation import BudgetAllocation, BudgetAllocationLine
from app.models.pay_recommendation import PayRecommendation, PayRecommendationComponent
from app.models.reporting_relationship import ReportingRelationship


async def build_budget_context(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    manager_user_id: uuid.UUID,
) -> str:
    """Return a markdown text block for a manager's budget context.

    Primary lookup: the BudgetAllocationLine where this manager is the
    recipient — the row that drives the MoM budget planner card and carries
    the exact figures shown on screen (allocated_amount, pool breakdown,
    jvre_rec_amount).

    Fallback: the manager's own BudgetAllocation (top-level MoM with no
    parent line).
    """
    line_row = (await db.execute(
        select(BudgetAllocationLine)
        .join(BudgetAllocation, BudgetAllocation.id == BudgetAllocationLine.allocation_id)
        .where(
            BudgetAllocation.tenant_id == tenant_id,
            BudgetAllocation.cycle_id  == cycle_id,
            BudgetAllocationLine.recipient_user_id == manager_user_id,
        )
    )).scalar_one_or_none()

    alloc_row = None
    if line_row is None:
        alloc_row = (await db.execute(
            select(BudgetAllocation).where(
                BudgetAllocation.tenant_id     == tenant_id,
                BudgetAllocation.cycle_id      == cycle_id,
                BudgetAllocation.owner_user_id == manager_user_id,
            )
        )).scalar_one_or_none()
        if alloc_row is None:
            return "No budget allocation found for this manager in the active cycle."

    if line_row is not None:
        currency         = line_row.currency_code
        allocated_amount = line_row.allocated_amount
        jvre_rec         = line_row.jvre_rec_amount
        base_pool        = line_row.base_pool
        variable_pool    = line_row.variable_pool
        lti_pool         = line_row.lti_grant_fmv_pool
        reserve_pool_amt = line_row.reserve_pool
    else:
        currency         = alloc_row.currency_code
        allocated_amount = alloc_row.budget_for_allocation
        jvre_rec         = None
        base_pool = variable_pool = lti_pool = reserve_pool_amt = None

    report_ids = list((await db.execute(
        select(ReportingRelationship.report_user_id).where(
            ReportingRelationship.tenant_id       == tenant_id,
            ReportingRelationship.cycle_id        == cycle_id,
            ReportingRelationship.manager_user_id == manager_user_id,
        )
    )).scalars().all())

    rec_status_map: dict = {}
    total_recommended = Decimal("0")
    if report_ids:
        rec_status_map = {
            r[0]: r[1]
            for r in (await db.execute(
                select(PayRecommendation.subject_user_id, PayRecommendation.status).where(
                    PayRecommendation.tenant_id        == tenant_id,
                    PayRecommendation.cycle_id         == cycle_id,
                    PayRecommendation.subject_user_id.in_(report_ids),
                )
            )).all()
        }
        spent_val = (await db.execute(
            select(func.coalesce(func.sum(PayRecommendationComponent.mgr_rec_value), 0))
            .join(PayRecommendation, PayRecommendation.id == PayRecommendationComponent.recommendation_id)
            .where(
                PayRecommendation.tenant_id       == tenant_id,
                PayRecommendation.cycle_id        == cycle_id,
                PayRecommendation.subject_user_id.in_(report_ids),
            )
        )).scalar_one()
        total_recommended = Decimal(str(spent_val))

    remaining = allocated_amount - total_recommended
    utilisation_pct = (
        round(float(total_recommended) / float(allocated_amount) * 100, 1)
        if allocated_amount else 0.0
    )
    submitted_count = sum(1 for s in rec_status_map.values() if s == "SUBMITTED")

    lines = [
        "# BUDGET CONTEXT — Manager's Allocation",
        f"Currency: {currency}",
        f"Allocated Budget: {allocated_amount} {currency}",
    ]
    if jvre_rec is not None:
        variance = allocated_amount - jvre_rec
        lines.append(f"JVRE Recommended Amount: {jvre_rec} {currency} (variance: {variance:+})")
    if base_pool is not None:
        lines += [
            f"Base Pool: {base_pool} {currency}",
            f"Variable Pool: {variable_pool} {currency}",
            f"LTI Grant Pool (FMV): {lti_pool} {currency}",
            f"Reserve Pool: {reserve_pool_amt} {currency}",
        ]
    lines += [
        f"Total Recommended by Manager (sum of mgr_rec_value): {total_recommended} {currency}",
        f"Remaining Headroom: {remaining} {currency}",
        f"Pool Utilisation: {utilisation_pct}%",
        f"\n## Team ({len(report_ids)} direct reports)",
        f"Pay recommendations submitted: {submitted_count} of {len(report_ids)}",
        f"Pay recommendations pending: {len(report_ids) - submitted_count}",
        "Recommendation statuses:",
    ]
    for uid, status in rec_status_map.items():
        lines.append(f"  - {uid}: {status}")

    return "\n".join(lines)


async def build_global_context(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
) -> str:
    """Return a markdown text block with org-wide compensation cycle aggregates."""
    agg = (await db.execute(
        select(
            func.count(BudgetAllocation.id),
            func.sum(BudgetAllocation.total_pool),
            func.sum(BudgetAllocation.budget_for_allocation),
        ).where(
            BudgetAllocation.tenant_id == tenant_id,
            BudgetAllocation.cycle_id  == cycle_id,
            BudgetAllocation.parent_allocation_id.is_(None),
        )
    )).one()
    mgr_count, total_pool, budget_for_alloc = agg

    alloc_status = {r[0]: r[1] for r in (await db.execute(
        select(BudgetAllocation.status, func.count(BudgetAllocation.id))
        .where(
            BudgetAllocation.tenant_id == tenant_id,
            BudgetAllocation.cycle_id  == cycle_id,
            BudgetAllocation.parent_allocation_id.is_(None),
        )
        .group_by(BudgetAllocation.status)
    )).all()}

    rec_status = {r[0]: r[1] for r in (await db.execute(
        select(PayRecommendation.status, func.count(PayRecommendation.id))
        .where(
            PayRecommendation.tenant_id == tenant_id,
            PayRecommendation.cycle_id  == cycle_id,
        )
        .group_by(PayRecommendation.status)
    )).all()}

    alloc_status_str = ", ".join(f"{k}: {v}" for k, v in alloc_status.items()) or "none"
    rec_status_str   = ", ".join(f"{k}: {v}" for k, v in rec_status.items()) or "none"

    return (
        f"# GLOBAL CONTEXT — Organisation Compensation Cycle\n"
        f"Managers in cycle: {mgr_count or 0}\n"
        f"Total budget pool: {total_pool or 0}\n"
        f"Budget for allocation: {budget_for_alloc or 0}\n"
        f"Budget allocation status: {alloc_status_str}\n"
        f"Pay recommendation status: {rec_status_str}\n"
    )
