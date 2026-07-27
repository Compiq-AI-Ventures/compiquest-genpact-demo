"""JVRE workspace read-side API.

All endpoints in this router are read-only in v0.1; write paths land
in Phases 4-6. Every endpoint requires an authenticated tenant user
(the existing ``get_tenant_context`` dependency) and uses
``get_tenant_scoped_db`` so the GUC + RLS policy fires.

Endpoint inventory
------------------

* ``GET /comp-cycles/active``                       — current active cycle.
* ``GET /comp-cycles/{cycle_id}``                   — read one cycle.
* ``GET /comp-cycles/{cycle_id}/my-budget-allocation``
                                                    — caller's own allocation.
* ``GET /comp-cycles/{cycle_id}/my-recommendations``
                                                    — subjects the caller is
                                                       responsible for.
* ``GET /budget-allocations/{allocation_id}/lines`` — per-recipient cards.
* ``GET /pay-recommendations/{recommendation_id}``  — full recommendation.
* ``GET /pay-recommendations/pending-review``       — caller's review queue,
                                                       grouped by submitter.
* ``GET /jvre/snapshots/{cycle_id}/{subject_user_id}``
                                                    — JVRE snapshot for one
                                                       subject.
* ``GET /users/{subject_user_id}/market-benchmark`` — market-pay reference.
* ``GET /users/{subject_user_id}/compensation-history``
                                                    — last N FYs of comp.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.reporting_chain_dependency import (
    require_in_reporting_chain,
)
from app.dependencies.scoped_db_dependency import get_tenant_scoped_db
from app.dependencies.tenant_dependency import (
    TenantContext,
    get_tenant_context,
)
from app.repositories import (
    compensation_history_repository,
    market_benchmark_repository,
    user_repository,
)
from app.schemas.jvre_workspace_schema import (
    AnnotationCreateRequest,
    BudgetAllocationLineUpdateRequest,
    BudgetAllocationUpdateRequest,
    CompensationHistoryResponse,
    CompensationHistoryRowResponse,
    CycleResponse,
    MarketBenchmarkResponse,
    PayRecommendationAnnotationResponse,
    PayRecommendationComponentUpdateRequest,
    PayRecommendationCreateRequest,
    PayRecommendationResponse,
    PendingReviewResponse,
    RecommendationReviseRequest,
)
from app.services import jvre_workspace_service
from app.utils.response_builder import success_response

logger = logging.getLogger(__name__)

def _request_context(request: Request) -> dict[str, Any]:
    """Extract audit metadata (request-id, IP, UA) from FastAPI request state.

    The request-id is injected by the middleware; it may be absent in tests.
    """
    return {
        "request_id": getattr(request.state, "request_id", None),
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


# Five routers grouped under the same tag so /docs renders them together.
cycle_router = APIRouter(prefix="/comp-cycles", tags=["jvre-workspace"])
# Budget allocation lines and actions live under /budget-allocations.
allocation_router = APIRouter(prefix="/budget-allocations", tags=["jvre-workspace"])
# Per-recommendation CRUD and review actions live under /pay-recommendations.
recommendation_router = APIRouter(prefix="/pay-recommendations", tags=["jvre-workspace"])
# JVRE snapshot read + rationale stream live under /jvre.
jvre_router = APIRouter(prefix="/jvre", tags=["jvre-workspace"])
# Per-subject reference data (benchmark, comp history) live under /users.
user_reference_router = APIRouter(prefix="/users", tags=["jvre-workspace"])


# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------
@cycle_router.get(
    "/active",
    summary="Get the active compensation cycle for the caller's tenant",
    responses={
        400: {"description": "Tenant context required"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller's tenant is suspended/disabled"},
        404: {"description": "No active cycle"},
    },
)
async def get_active_cycle(
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    cycle = await jvre_workspace_service.get_active_cycle(db, ctx.active_tenant_id)
    return success_response(
        message="Active cycle",
        data=CycleResponse.model_validate(cycle),
    )


@cycle_router.get(
    "/{cycle_id}",
    summary="Get one compensation cycle",
    responses={
        400: {"description": "Tenant context required"},
        401: {"description": "Missing or invalid Bearer token"},
        404: {"description": "Cycle not found in this tenant"},
    },
)
async def get_cycle(
    cycle_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    cycle = await jvre_workspace_service.get_cycle(db, ctx.active_tenant_id, cycle_id)
    return success_response(message="Cycle", data=CycleResponse.model_validate(cycle))


@cycle_router.get(
    "/{cycle_id}/my-budget-allocation",
    summary="Caller's own budget allocation for this cycle",
    responses={
        400: {"description": "Tenant context required"},
        401: {"description": "Missing or invalid Bearer token"},
        404: {"description": "No allocation exists for the caller in this cycle"},
    },
)
async def get_my_budget_allocation(
    cycle_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    allocation = await jvre_workspace_service.get_my_budget_allocation(
        db, ctx.active_tenant_id, cycle_id, ctx.user.id
    )
    return success_response(
        message="My budget allocation",
        data=allocation,
    )


@cycle_router.get(
    "/{cycle_id}/my-recommendations",
    summary="Subjects the caller is responsible for in this cycle",
    responses={
        400: {"description": "Tenant context required"},
        401: {"description": "Missing or invalid Bearer token"},
    },
)
async def list_my_recommendations(
    cycle_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    rows = await jvre_workspace_service.list_my_recommendation_subjects(
        db, ctx.active_tenant_id, cycle_id, ctx.user.id
    )
    return success_response(
        message="My recommendations",
        data={
            "items": rows,
            "total": len(rows),
        },
    )


# ---------------------------------------------------------------------------
# Budget allocation lines (right-panel cards)
# ---------------------------------------------------------------------------
@allocation_router.get(
    "/{allocation_id}/lines",
    summary="Per-recipient lines on a budget allocation",
    responses={
        400: {"description": "Tenant context required"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller is not the owner of this allocation"},
        404: {"description": "Allocation not found in this tenant"},
    },
)
async def list_allocation_lines(
    allocation_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    lines = await jvre_workspace_service.list_allocation_lines(
        db, ctx.active_tenant_id, allocation_id, ctx.user.id
    )
    return success_response(
        message="Allocation lines",
        data={"items": lines, "total": len(lines)},
    )


@allocation_router.get(
    "/{allocation_id}/team-risk-snapshot",
    summary="Aggregated IC-level risk snapshot for the MoM dashboard",
    responses={
        400: {"description": "Tenant context required"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller is not the owner of this allocation"},
        404: {"description": "Allocation not found in this tenant"},
    },
)
async def get_team_risk_snapshot(
    allocation_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    snapshot = await jvre_workspace_service.get_team_risk_snapshot(
        db, ctx.active_tenant_id, allocation_id, ctx.user.id
    )
    return success_response(
        message="Team risk snapshot",
        data=snapshot,
    )


# ---------------------------------------------------------------------------
# Budget allocation — write endpoints (Phase 4)
# ---------------------------------------------------------------------------
@cycle_router.put(
    "/{cycle_id}/my-budget-allocation",
    summary="Update the strategic_reserve on caller's allocation",
    responses={
        400: {
            "description": (
                "Tenant context required / strategic reserve exceeds pool / "
                "allocation no longer editable"
            )
        },
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller is not the owner of this allocation"},
        404: {"description": "No allocation exists for the caller in this cycle"},
    },
)
async def update_my_budget_allocation(
    cycle_id: uuid.UUID,
    body: BudgetAllocationUpdateRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    # Resolve the caller's allocation id, then delegate to the
    # allocation-id-rooted update.
    alloc = await jvre_workspace_service.get_my_budget_allocation(
        db, ctx.active_tenant_id, cycle_id, ctx.user.id
    )
    await jvre_workspace_service.update_budget_allocation(
        db,
        ctx.active_tenant_id,
        alloc.id,
        body,
        caller_user_id=ctx.user.id,
    )
    refreshed = await jvre_workspace_service.get_my_budget_allocation(
        db, ctx.active_tenant_id, cycle_id, ctx.user.id
    )
    return success_response(
        message="Budget allocation updated",
        data=refreshed,
    )


@allocation_router.post(
    "/{allocation_id}/align-with-jvre",
    summary=("Initialize-or-reset every line on this allocation to JVRE rec"),
    responses={
        400: {"description": "Tenant context required / not editable"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller is not the owner of this allocation"},
        404: {"description": "Allocation not found in this tenant"},
    },
)
async def align_lines_with_jvre(
    allocation_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    # DESTRUCTIVE on every call — resets ALL existing line overrides back
    # to JVRE rec. Use GET /lines to inspect without mutating.
    await jvre_workspace_service.align_lines_with_jvre(
        db,
        ctx.active_tenant_id,
        allocation_id,
        caller_user_id=ctx.user.id,
    )
    # Re-render the right panel.
    lines = await jvre_workspace_service.list_allocation_lines(
        db, ctx.active_tenant_id, allocation_id, ctx.user.id
    )
    return success_response(
        message="Lines aligned with JVRE",
        data={
            "items": lines,
            "total": len(lines),
        },
    )


@allocation_router.put(
    "/{allocation_id}/lines/{line_id}",
    summary="Update one recipient's line on a budget allocation",
    responses={
        400: {"description": "Tenant context required / not editable"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller is not the owner of this allocation"},
        404: {"description": "Allocation or line not found in this tenant"},
    },
)
async def update_allocation_line(
    allocation_id: uuid.UUID,
    line_id: uuid.UUID,
    body: BudgetAllocationLineUpdateRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    await jvre_workspace_service.update_allocation_line(
        db,
        ctx.active_tenant_id,
        allocation_id,
        line_id,
        body,
        caller_user_id=ctx.user.id,
    )
    # Returning all lines keeps the right-panel state coherent (sum
    # totals, "1 of 4 Completed" counter, etc.).
    lines = await jvre_workspace_service.list_allocation_lines(
        db, ctx.active_tenant_id, allocation_id, ctx.user.id
    )
    return success_response(
        message="Allocation line updated",
        data={
            "items": lines,
            "total": len(lines),
        },
    )


@allocation_router.post(
    "/{allocation_id}/lines/{line_id}/refresh-view",
    summary="Reset one line back to its JVRE-recommended amount",
    responses={
        400: {"description": "Tenant context required / not editable"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller is not the owner of this allocation"},
        404: {"description": "Allocation or line not found in this tenant"},
    },
)
async def refresh_line_to_jvre(
    allocation_id: uuid.UUID,
    line_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    await jvre_workspace_service.refresh_line_to_jvre(
        db,
        ctx.active_tenant_id,
        allocation_id,
        line_id,
        caller_user_id=ctx.user.id,
    )
    lines = await jvre_workspace_service.list_allocation_lines(
        db, ctx.active_tenant_id, allocation_id, ctx.user.id
    )
    return success_response(
        message="Line reset to JVRE rec",
        data={
            "items": lines,
            "total": len(lines),
        },
    )


@allocation_router.post(
    "/{allocation_id}/submit",
    status_code=status.HTTP_200_OK,
    summary="Lock the allocation and cascade child allocations to recipients",
    responses={
        400: {
            "description": (
                "Tenant context required / not editable / sum exceeds budget "
                "/ missing lines for some direct reports"
            )
        },
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller is not the owner of this allocation"},
        404: {"description": "Allocation not found in this tenant"},
    },
)
async def submit_budget_allocation(
    request: Request,
    allocation_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    alloc = await jvre_workspace_service.submit_budget_allocation(
        db,
        ctx.active_tenant_id,
        allocation_id,
        caller_user_id=ctx.user.id,
        **_request_context(request),
    )
    # Re-render the left panel post-submit (status flips, completion
    # timestamp shows).
    refreshed = await jvre_workspace_service.get_my_budget_allocation(
        db, ctx.active_tenant_id, alloc.cycle_id, ctx.user.id
    )
    return success_response(
        message="Budget allocation submitted",
        data=refreshed,
    )


# ---------------------------------------------------------------------------
# Pay recommendations
# ---------------------------------------------------------------------------
@recommendation_router.get(
    "/pending-review",
    summary="Caller's review queue, grouped by submitter",
    responses={
        400: {"description": "Tenant context required"},
        401: {"description": "Missing or invalid Bearer token"},
    },
)
async def list_pending_review(
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    cycle = await jvre_workspace_service.get_active_cycle(db, ctx.active_tenant_id)
    response = await jvre_workspace_service.list_pending_review(
        db, ctx.active_tenant_id, cycle.id, ctx.user.id
    )
    return success_response(
        message="Pending review",
        data=PendingReviewResponse.model_validate(response),
    )


@recommendation_router.get(
    "/{recommendation_id}",
    summary="Full snapshot of one pay recommendation",
    responses={
        400: {"description": "Tenant context required"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Subject not in caller's reporting chain"},
        404: {"description": "Recommendation not found in this tenant"},
    },
)
async def get_recommendation(
    recommendation_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    rec = await jvre_workspace_service.get_recommendation(
        db, ctx.active_tenant_id, recommendation_id, ctx.user.id
    )
    return success_response(
        message="Pay recommendation",
        data=PayRecommendationResponse.model_validate(rec),
    )


# ---------------------------------------------------------------------------
# Pay recommendations — write endpoints (Phase 5)
# ---------------------------------------------------------------------------
@cycle_router.post(
    "/{cycle_id}/recommendations",
    status_code=status.HTTP_201_CREATED,
    summary="Open (or get-existing) the caller's draft recommendation for a subject",
    responses={
        400: {"description": "Tenant context required"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Subject is not in caller's direct reports"},
        404: {"description": "Cycle not found in this tenant"},
    },
)
async def create_or_get_recommendation(
    cycle_id: uuid.UUID,
    body: PayRecommendationCreateRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    rec = await jvre_workspace_service.get_or_create_recommendation(
        db,
        ctx.active_tenant_id,
        cycle_id,
        actor_user_id=ctx.user.id,
        subject_user_id=body.subject_user_id,
    )
    response = await jvre_workspace_service.get_recommendation(
        db, ctx.active_tenant_id, rec.id, ctx.user.id
    )
    return success_response(
        message="Pay recommendation",
        data=PayRecommendationResponse.model_validate(response),
    )


@recommendation_router.put(
    "/{recommendation_id}/components/{component}",
    summary="Set one cell on a recommendation card",
    responses={
        400: {
            "description": (
                "Tenant context required / invalid component / recommendation not editable"
            )
        },
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller is not the recommendation's actor"},
        404: {"description": "Recommendation not found in this tenant"},
    },
)
async def update_recommendation_component(
    recommendation_id: uuid.UUID,
    component: str,
    body: PayRecommendationComponentUpdateRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    await jvre_workspace_service.update_recommendation_component(
        db,
        ctx.active_tenant_id,
        recommendation_id,
        component,
        body,
        caller_user_id=ctx.user.id,
    )
    refreshed = await jvre_workspace_service.get_recommendation(
        db, ctx.active_tenant_id, recommendation_id, ctx.user.id
    )
    return success_response(
        message="Component updated",
        data=PayRecommendationResponse.model_validate(refreshed),
    )


@recommendation_router.post(
    "/{recommendation_id}/align-with-jvre",
    summary="Reset every component on this recommendation to JVRE rec",
    responses={
        400: {"description": "Tenant context required / not editable"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller is not the recommendation's actor"},
        404: {"description": "Recommendation not found in this tenant"},
    },
)
async def align_recommendation_with_jvre(
    recommendation_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    await jvre_workspace_service.align_recommendation_with_jvre(
        db,
        ctx.active_tenant_id,
        recommendation_id,
        caller_user_id=ctx.user.id,
    )
    refreshed = await jvre_workspace_service.get_recommendation(
        db, ctx.active_tenant_id, recommendation_id, ctx.user.id
    )
    return success_response(
        message="Recommendation aligned with JVRE",
        data=PayRecommendationResponse.model_validate(refreshed),
    )


@recommendation_router.post(
    "/{recommendation_id}/save",
    summary="Stamp Saved on this recommendation (advances completed counter)",
    responses={
        400: {"description": "Tenant context required / not editable"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller is not the recommendation's actor"},
        404: {"description": "Recommendation not found in this tenant"},
    },
)
async def save_recommendation(
    recommendation_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    await jvre_workspace_service.save_recommendation(
        db,
        ctx.active_tenant_id,
        recommendation_id,
        caller_user_id=ctx.user.id,
    )
    refreshed = await jvre_workspace_service.get_recommendation(
        db, ctx.active_tenant_id, recommendation_id, ctx.user.id
    )
    return success_response(
        message="Recommendation saved",
        data=PayRecommendationResponse.model_validate(refreshed),
    )


@cycle_router.post(
    "/{cycle_id}/my-recommendations/submit",
    summary="Submit every DRAFT recommendation the caller authored",
    responses={
        400: {
            "description": (
                "Tenant context required / cycle not active / missing "
                "recommendations for some direct reports"
            )
        },
        401: {"description": "Missing or invalid Bearer token"},
    },
)
async def submit_my_recommendations(
    request: Request,
    cycle_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    await jvre_workspace_service.submit_my_recommendations(
        db,
        ctx.active_tenant_id,
        cycle_id,
        caller_user_id=ctx.user.id,
        **_request_context(request),
    )
    rows = await jvre_workspace_service.list_my_recommendation_subjects(
        db, ctx.active_tenant_id, cycle_id, ctx.user.id
    )
    return success_response(
        message="Pay recommendations submitted",
        data={
            "items": rows,
            "total": len(rows),
        },
    )


# ---------------------------------------------------------------------------
# Pay recommendations — review endpoints (Phase 6)
# ---------------------------------------------------------------------------
@recommendation_router.post(
    "/{recommendation_id}/approve",
    summary="Approve a recommendation as the upstream reviewer",
    responses={
        400: {"description": "Tenant context required / not in a reviewable status"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller is not the upstream reviewer"},
        404: {"description": "Recommendation not found in this tenant"},
    },
)
async def approve_recommendation(
    request: Request,
    recommendation_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    await jvre_workspace_service.approve_recommendation(
        db,
        ctx.active_tenant_id,
        recommendation_id,
        caller_user_id=ctx.user.id,
        **_request_context(request),
    )
    refreshed = await jvre_workspace_service.get_recommendation(
        db, ctx.active_tenant_id, recommendation_id, ctx.user.id
    )
    return success_response(
        message="Recommendation approved",
        data=PayRecommendationResponse.model_validate(refreshed),
    )


@recommendation_router.post(
    "/{recommendation_id}/revise",
    summary="Mark a recommendation REVISED + append the reviewer's annotation",
    responses={
        400: {"description": "Tenant context required / not in a reviewable status"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller is not the upstream reviewer"},
        404: {"description": "Recommendation not found in this tenant"},
    },
)
async def revise_recommendation(
    request: Request,
    recommendation_id: uuid.UUID,
    body: RecommendationReviseRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    await jvre_workspace_service.revise_recommendation(
        db,
        ctx.active_tenant_id,
        recommendation_id,
        body,
        caller_user_id=ctx.user.id,
        **_request_context(request),
    )
    refreshed = await jvre_workspace_service.get_recommendation(
        db, ctx.active_tenant_id, recommendation_id, ctx.user.id
    )
    return success_response(
        message="Recommendation revised",
        data=PayRecommendationResponse.model_validate(refreshed),
    )


@recommendation_router.post(
    "/{recommendation_id}/annotations",
    status_code=status.HTTP_201_CREATED,
    summary="Append a free-text annotation to a recommendation",
    responses={
        400: {"description": "Tenant context required"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Subject is not in caller's reporting chain"},
        404: {"description": "Recommendation not found in this tenant"},
    },
)
async def add_recommendation_annotation(
    recommendation_id: uuid.UUID,
    body: AnnotationCreateRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    annotation = await jvre_workspace_service.add_recommendation_annotation(
        db,
        ctx.active_tenant_id,
        recommendation_id,
        body,
        caller_user_id=ctx.user.id,
    )
    actor = await user_repository.get_by_id_tenant_scoped(
        db, ctx.active_tenant_id, annotation.actor_user_id
    )
    last = f" {actor.last_name}" if actor and actor.last_name else ""
    actor_name = f"{actor.first_name}{last}" if actor else ""
    return success_response(
        message="Annotation added",
        data=PayRecommendationAnnotationResponse(
            id=annotation.id,
            actor_user_id=annotation.actor_user_id,
            actor_name=actor_name,
            text=annotation.text,
            created_at=annotation.created_at,
        ),
    )


# ---------------------------------------------------------------------------
# JVRE snapshot
# ---------------------------------------------------------------------------
@jvre_router.get(
    "/snapshots/{cycle_id}/{subject_user_id}",
    summary="JVRE snapshot for one (cycle, subject)",
    responses={
        400: {"description": "Tenant context required"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Subject not in caller's reporting chain"},
        404: {"description": "No JVRE snapshot for this subject in this cycle"},
    },
)
async def get_jvre_snapshot(
    cycle_id: uuid.UUID,
    subject_user_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    _: None = Depends(require_in_reporting_chain),
) -> dict[str, Any]:
    data = await jvre_workspace_service.build_jvre_snapshot_response(
        db, ctx.active_tenant_id, cycle_id, subject_user_id
    )
    return success_response(message="JVRE snapshot", data=data)



# ---------------------------------------------------------------------------
# Reference data — market benchmark + compensation history
# ---------------------------------------------------------------------------
@user_reference_router.get(
    "/{subject_user_id}/market-benchmark",
    summary="Market-pay reference for a subject",
    responses={
        400: {"description": "Tenant context required"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Subject not in caller's reporting chain"},
        404: {"description": "No benchmark for this subject"},
    },
)
async def get_market_benchmark(
    subject_user_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    _: None = Depends(require_in_reporting_chain),
) -> dict[str, Any]:
    bench = await market_benchmark_repository.get_for_subject(
        db, ctx.active_tenant_id, subject_user_id
    )
    if bench is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Market benchmark not found.",
        )
    return success_response(
        message="Market benchmark",
        data=MarketBenchmarkResponse.model_validate(bench),
    )


@user_reference_router.get(
    "/{subject_user_id}/compensation-history",
    summary="Historical FY rows for a subject (newest first)",
    responses={
        400: {"description": "Tenant context required"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Subject not in caller's reporting chain"},
    },
)
async def get_compensation_history(
    subject_user_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    _: None = Depends(require_in_reporting_chain),
) -> dict[str, Any]:
    rows = await compensation_history_repository.list_for_subject(
        db, ctx.active_tenant_id, subject_user_id
    )
    return success_response(
        message="Compensation history",
        data=CompensationHistoryResponse(
            subject_user_id=subject_user_id,
            rows=[CompensationHistoryRowResponse.model_validate(r) for r in rows],
        ),
    )
