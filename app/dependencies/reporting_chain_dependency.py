"""Reporting-chain authorization dependency.

The JVRE workspace endpoints that expose data about a specific
``subject_user_id`` are gated by "the caller must be in the subject's
reporting chain for the active cycle". This dependency wraps that
check in a clean FastAPI dep so routes can declare it instead of
calling the service helper inline.

Use it on routes whose path carries ``subject_user_id`` directly. For
endpoints rooted on a ``recommendation_id`` the chain check is
nuanced (the subject lives inside the recommendation row plus
override rules apply), so those routes use the service-layer
``_classify_caller_or_raise`` / ``_caller_can_read_recommendation``
helpers instead.

Wire it into a route like::

    @router.get("/users/{subject_user_id}/market-benchmark")
    async def get_market_benchmark(
        subject_user_id: uuid.UUID,
        ctx: TenantContext = Depends(get_tenant_context),
        db: AsyncSession = Depends(get_tenant_scoped_db),
        _: None = Depends(require_in_reporting_chain),
    ) -> dict[str, Any]:
        ...

The dep itself reads ``subject_user_id`` out of the path (FastAPI
auto-resolves by name), runs the chain check via the service helper,
and returns ``None`` (the route doesn't need the value back — it's
already in the path).
"""

from __future__ import annotations

import uuid

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.scoped_db_dependency import get_tenant_scoped_db
from app.dependencies.tenant_dependency import (
    TenantContext,
    get_tenant_context,
)
from app.services import jvre_workspace_service


async def require_in_reporting_chain(
    subject_user_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> None:
    """Reject the request unless the caller is in the subject's
    reporting chain for the active cycle.

    Caller-IS-the-subject is allowed (a user can always read their own
    data). Otherwise the caller must be the subject's direct manager
    OR the manager's manager (covers MoM-can-see-IC). Cross-tenant is
    impossible by construction (the GUC + RLS layer sees the wrong
    tenant's rows as non-existent).

    Returns ``None`` — the route already has ``subject_user_id`` from
    its path. Raises :class:`SubjectNotInReportingChainError` (403) on
    failure.
    """
    await jvre_workspace_service.assert_subject_in_reporting_chain(
        db, ctx.active_tenant_id, ctx.user.id, subject_user_id
    )
