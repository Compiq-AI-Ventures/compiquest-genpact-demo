"""P&L Head Executive Summary — org-wide dashboard read.

Single endpoint, ``PNL_HEAD``-gated. See ``pnl_dashboard_service.py``
for the verified derivations.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import RoleCode
from app.dependencies.role_dependency import require_tenant_roles
from app.dependencies.scoped_db_dependency import get_tenant_scoped_db
from app.dependencies.tenant_dependency import TenantContext
from app.services import pnl_dashboard_service
from app.utils.response_builder import success_response

router = APIRouter(prefix="/pnl-dashboard", tags=["pnl-dashboard"])

_READ_DEPENDENCY = require_tenant_roles([RoleCode.PNL_HEAD])


@router.get(
    "/executive-summary",
    summary="Org-wide Executive Summary for the P&L Head dashboard",
    responses={
        400: {"description": "Tenant context required"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller lacks the PNL_HEAD role"},
    },
)
async def get_executive_summary(
    ctx: TenantContext = Depends(_READ_DEPENDENCY),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    summary = await pnl_dashboard_service.get_executive_summary(db, ctx.active_tenant_id)
    return success_response(
        message="P&L executive summary",
        data=summary,
    )
