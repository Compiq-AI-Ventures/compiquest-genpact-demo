"""P&L Head / C&B Executive Summary — role-aware dashboard read.

Two callers hit the same endpoint:

* ``PNL_HEAD`` — sees KPIs scoped to their own business unit, derived
  from ``users.department_id → departments.name``.
* ``C_AND_B`` — sees the org-wide aggregates (no BU filter).

Every KPI query in ``pnl_dashboard_service`` takes an optional
``business_unit`` filter; this router decides which value to pass in
based on the caller's role.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import RoleCode
from app.dependencies.role_dependency import require_tenant_roles
from app.dependencies.scoped_db_dependency import get_tenant_scoped_db
from app.dependencies.tenant_dependency import TenantContext
from app.services import pnl_dashboard_service
from app.utils.response_builder import success_response

router = APIRouter(prefix="/pnl-dashboard", tags=["pnl-dashboard"])

# PNL_HEAD reads their BU-scoped view; C_AND_B reads the org-wide one.
# Every other role that lands here is either an admin or shouldn't be
# hitting a P&L dashboard directly — 403 for them.
_READ_DEPENDENCY = require_tenant_roles([RoleCode.PNL_HEAD, RoleCode.C_AND_B])


async def _bu_for_caller(
    db: AsyncSession, ctx: TenantContext
) -> str | None:
    """Return the BU string the caller should be scoped to, or ``None`` for
    an org-wide view.

    * ``PNL_HEAD`` → the caller's own department name (looked up from
      ``users.department_id → departments.name``). The name must match a
      ``genpact_employee_master.business_unit`` value for the seeded data,
      which it does today (FP&A, Accounts Payable, etc. are shared).
    * ``C_AND_B`` → org-wide.
    """
    codes = ctx.role_profile.tenant_roles
    if RoleCode.PNL_HEAD not in codes:
        return None

    dept_id = ctx.user.department_id
    if dept_id is None:
        # A P&L Head with no department can't be BU-scoped — fall through
        # to org-wide rather than 500 mid-demo.
        return None

    name = (
        await db.execute(
            text(
                "SELECT name FROM departments "
                "WHERE tenant_id = :tenant_id AND id = :dept_id"
            ),
            {"tenant_id": str(ctx.active_tenant_id), "dept_id": str(dept_id)},
        )
    ).scalar_one_or_none()
    return name


@router.get(
    "/executive-summary",
    summary="Executive Summary (BU-scoped for PNL_HEAD, org-wide for C&B)",
    responses={
        400: {"description": "Tenant context required"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller lacks the PNL_HEAD or C_AND_B role"},
    },
)
async def get_executive_summary(
    ctx: TenantContext = Depends(_READ_DEPENDENCY),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    business_unit = await _bu_for_caller(db, ctx)
    summary = await pnl_dashboard_service.get_executive_summary(
        db, ctx.active_tenant_id, business_unit=business_unit,
    )
    return success_response(
        message="Executive summary",
        data=summary,
    )
