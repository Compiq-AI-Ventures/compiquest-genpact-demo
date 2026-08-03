"""JVRE workspace orchestration — read-side composition + scoping.

Read-only in v0.1; write paths land in Phases 4-6. Most endpoints in
Phase 3 are denormalize-and-return: the service composes a
:class:`PayRecommendationResponse` from rows in 4 tables, attaches the
JVRE snapshot, and computes a couple of derived fields the screen
needs (final value, deviation%, +X% over current).

Scoping
-------
Every read endpoint is gated by:

* **Tenant** — handled by ``get_tenant_scoped_db`` upstream; the GUC
  + RLS ensures cross-tenant rows are invisible.
* **Reporting chain** — the service-layer functions take the caller's
  ``user_id`` and check against ``reporting_relationships`` for the
  active cycle. A clean ``require_in_reporting_chain`` dependency
  lands in Phase 7; for v0.1 the inline check is sufficient.

Errors
------
* :class:`RecommendationNotFoundError` — 404.
* :class:`SubjectNotInReportingChainError` — 403.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import RoleCode
from app.models.budget_allocation import (
    BudgetAllocation,
    BudgetAllocationLine,
    BudgetAllocationStatus,
)
from app.models.compensation_cycle import CompensationCycle
from app.models.department import Department
from app.models.iquest_engine_output import IquestEngineOutput
from app.models.jvre_snapshot import JvrePromotionReadiness, JvreSnapshot
from app.models.pay_recommendation import (
    PayRecommendation,
    PayRecommendationAnnotation,
    PayRecommendationComponent,
    PayRecommendationOverride,
)
from app.models.user import User
from app.repositories import (
    budget_allocation_repository,
    compensation_cycle_repository,
    jvre_rationale_repository,
    jvre_snapshot_repository,
    market_benchmark_repository,
    pay_recommendation_repository,
    reporting_relationship_repository,
    user_repository,
)
from app.schemas.jvre_workspace_schema import (
    AnnotationCreateRequest,
    BudgetAllocationLineResponse,
    BudgetAllocationLineUpdateRequest,
    BudgetAllocationUpdateRequest,
    JvreReserveRecommendation,
    JvreSnapshotResponse,
    MyBudgetAllocationResponse,
    MyRecommendationSubjectResponse,
    PayRecommendationAnnotationResponse,
    PayRecommendationComponentResponse,
    PayRecommendationComponentUpdateRequest,
    PayRecommendationOverrideResponse,
    PayRecommendationResponse,
    PendingReviewResponse,
    PendingReviewSubmitter,
    RecommendationReviseRequest,
    RiskSnapshotAllMember,
    RiskSnapshotCriticalMember,
    RiskSnapshotGroup,
    RiskSnapshotManagerGroup,
    RiskSnapshotMember,
    RiskSnapshotPromoMember,
    RiskSummaryItem,
    TeamRiskSnapshotResponse,
)
from app.services import audit_log_service
from app.services.jvre_workspace_errors import (
    AllocationExceedsBudgetError,
    BudgetAllocationLineNotFoundError,
    BudgetAllocationNotEditableError,
    BudgetAllocationNotFoundError,
    CycleNotFoundError,
    InvalidPayComponentError,
    MissingAllocationLinesError,
    NotAllocationOwnerError,
    RecommendationNotEditableError,
    RecommendationNotFoundError,
    StrategicReserveExceedsPoolError,
    SubjectNotInReportingChainError,
)

# ---------------------------------------------------------------------------
# Reserve recommendation defaults (v0.1 heuristic)
# ---------------------------------------------------------------------------
# Until the real JVRE engine produces these per allocation, we infer
# the band from the owner's tier. Match the seed script's constants.
_MOM_RESERVE_RANGE = (0.10, 0.13)
_MOP_RESERVE_RANGE = (0.04, 0.08)


def _is_mom(role_codes: set[str]) -> bool:
    """True if the role set includes MoM or CFO — checks codes, not org position."""
    return RoleCode.MANAGER_OF_MANAGERS in role_codes or RoleCode.CFO in role_codes


def _is_mop(role_codes: set[str]) -> bool:
    """True if the role set includes MANAGER — checks codes, not org position."""
    return RoleCode.MANAGER in role_codes


async def _user_role_codes(db: AsyncSession, user_id: uuid.UUID) -> set[str]:
    """Set of role codes held by a user. Tenant-irrelevant — already scoped.

    Explicit refresh is required because the async session won't eager-load
    relationships on a ``db.get()`` result.
    """
    user = await db.get(User, user_id)
    if user is None:
        return set()
    await db.refresh(user, ["roles"])
    return {r.code for r in user.roles}


def _reserve_band_for(role_codes: set[str]) -> tuple[float, float]:
    if _is_mom(role_codes):
        return _MOM_RESERVE_RANGE
    if _is_mop(role_codes):
        return _MOP_RESERVE_RANGE
    # Fallback: same as MoP. Should never hit in v0.1 since other tiers
    # don't own a budget allocation.
    return _MOP_RESERVE_RANGE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _user_name(db: AsyncSession, user_id: uuid.UUID) -> str:
    user = await db.get(User, user_id)
    if user is None:
        return ""
    last = f" {user.last_name}" if user.last_name else ""
    return f"{user.first_name}{last}"


def _final_value(component: PayRecommendationComponent) -> Decimal | None:
    """``mom_rec_value`` if not NULL else ``mgr_rec_value`` if not NULL
    else ``jvre_rec_value``.

    Mirrors the spec's "lineage" rule. Computed in code so the
    ``final_value`` column doesn't need to be materialized.
    """
    if component.mom_rec_value is not None:
        return component.mom_rec_value
    if component.mgr_rec_value is not None:
        return component.mgr_rec_value
    return component.jvre_rec_value


def _component_to_response(
    component: PayRecommendationComponent,
) -> PayRecommendationComponentResponse:
    return PayRecommendationComponentResponse(
        component=component.component,
        current_value=component.current_value,
        jvre_rec_value=component.jvre_rec_value,
        mgr_rec_value=component.mgr_rec_value,
        mom_rec_value=component.mom_rec_value,
        final_value=_final_value(component),
        currency_code=component.currency_code,
    )


# ---------------------------------------------------------------------------
# Subject enrichment helpers (shared by list_my_recommendation_subjects
# and _submissions_to_summary so both screens get identical field coverage)
# ---------------------------------------------------------------------------
async def _bulk_fetch_subject_data(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    subject_ids: list[uuid.UUID],
) -> tuple[
    dict[uuid.UUID, JvreSnapshot],
    dict[uuid.UUID, object],
    dict[uuid.UUID, User],
]:
    """Bulk fetch snapshots, benchmarks and users for a list of subject IDs.
    Returns three dicts keyed by subject_user_id (as normalized str).

    UUIDs are normalised to ``str(uuid.UUID(...))`` to avoid asyncpg
    type-mismatch failures when comparing ORM-returned UUIDs to dict keys.
    """

    # Normalize all UUIDs to Python uuid.UUID to avoid asyncpg type mismatches
    def norm(x):
        return str(uuid.UUID(str(x)))

    snapshots = await jvre_snapshot_repository.list_for_subjects(
        db, tenant_id, cycle_id, subject_ids
    )
    snapshot_by_subject: dict[str, JvreSnapshot] = {norm(s.subject_user_id): s for s in snapshots}

    benchmarks = await market_benchmark_repository.get_for_subjects(db, tenant_id, subject_ids)
    benchmark_by_subject: dict[str, object] = {norm(b.subject_user_id): b for b in benchmarks}
    # Defense-in-depth: scope user lookup to the caller's tenant explicitly.
    # users table has no RLS — explicit tenant filter is required.
    users_map = await user_repository.batch_by_ids_tenant_scoped(
        db, tenant_id, subject_ids, load_department=True
    )
    user_by_id: dict[str, User] = {norm(u.id): u for u in users_map.values()}
    return snapshot_by_subject, benchmark_by_subject, user_by_id


def _snap_vals(snapshot: JvreSnapshot | None) -> dict:
    """Return snapshot fields as a plain dict. Returns ``{}`` for None so
    callers can safely ``.get()`` without a None check."""
    if snapshot is None:
        return {}
    return {
        "subject_level": snapshot.recommended_level,
        "current_level": snapshot.recommended_level,
        "recommended_level": snapshot.recommended_level,
        "criticality": snapshot.criticality,
        "market_position": snapshot.market_position,
        "promotion_readiness": snapshot.promotion_readiness,
        "risk_callout_text": snapshot.risk_callout_text,
        "comp_base_pay": snapshot.recommended_base,
        "comp_variable_pay": snapshot.recommended_variable,
        "comp_lti_fmv": snapshot.recommended_lti_fmv,
        "comp_other_rewards": snapshot.recommended_other_rewards,
    }


def _build_subject_response(
    *,
    subject_id: uuid.UUID,
    subject: User | None,
    snapshot: JvreSnapshot | None,
    benchmark: object | None,
    rec: PayRecommendation | None,
    final_total: Decimal | None,
    mgr_total: Decimal | None,
    mom_total: Decimal | None,
    jvre_total: Decimal | None,
    deviation_pct: float | None,
) -> MyRecommendationSubjectResponse:
    """Pure sync builder — all data pre-fetched by the caller.
    Single source of truth for MyRecommendationSubjectResponse field mapping.
    Used by both list_my_recommendation_subjects and _submissions_to_summary."""
    sv = _snap_vals(snapshot)
    subject_name = (
        f"{subject.first_name} {subject.last_name or ''}".strip() if subject else "No Response"
    )
    subject_dept = subject.department.name if subject and subject.department else None
    market_gap = benchmark.target_pay - benchmark.current_pay if benchmark else None
    return MyRecommendationSubjectResponse(
        subject_user_id=subject_id,
        subject_name=subject_name,
        subject_level=sv.get("subject_level"),
        subject_department=subject_dept,
        job_title=subject.job_title if subject else None,
        recommendation_id=rec.id if rec else None,
        status=rec.status if rec else "PENDING",
        final_total_rewards=final_total,
        jvre_rec_total=jvre_total,
        deviation_pct=deviation_pct,
        mgr_rec_total=mgr_total,
        mom_rec_total=mom_total,
        criticality=sv.get("criticality"),
        market_position=sv.get("market_position"),
        promotion_readiness=sv.get("promotion_readiness"),
        compa_ratio=benchmark.compa_ratio if benchmark else None,
        current_level=sv.get("current_level"),
        risk_callout_text=sv.get("risk_callout_text"),
        recommended_level=sv.get("recommended_level"),
        market_gap=market_gap,
        comp_base_pay=sv.get("comp_base_pay"),
        comp_variable_pay=sv.get("comp_variable_pay"),
        comp_lti_fmv=sv.get("comp_lti_fmv"),
        comp_other_rewards=sv.get("comp_other_rewards"),
    )


# ---------------------------------------------------------------------------
# Cycle resolution
# ---------------------------------------------------------------------------
async def get_active_cycle(db: AsyncSession, tenant_id: uuid.UUID) -> CompensationCycle:
    cycle = await compensation_cycle_repository.get_active(db, tenant_id)
    if cycle is None:
        raise CycleNotFoundError()
    return cycle


async def get_cycle(
    db: AsyncSession, tenant_id: uuid.UUID, cycle_id: uuid.UUID
) -> CompensationCycle:
    cycle = await compensation_cycle_repository.get_for_tenant(db, tenant_id, cycle_id)
    if cycle is None:
        raise CycleNotFoundError()
    return cycle


# ---------------------------------------------------------------------------
# JVRE snapshot enrichment (single subject)
# ---------------------------------------------------------------------------
def _tcc(base: Decimal | None, variable: Decimal | None) -> Decimal | None:
    """Total cash compensation from two nullable components. Returns None if both absent."""
    if base is None and variable is None:
        return None
    return (base or Decimal("0")) + (variable or Decimal("0"))


async def build_jvre_snapshot_response(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    subject_user_id: uuid.UUID,
) -> JvreSnapshotResponse:
    """Load a JVRE snapshot and enrich it with user + benchmark data.

    Raises ``RecommendationNotFoundError`` (404) when no snapshot exists.
    """
    snapshot = await jvre_snapshot_repository.get_for_subject(
        db, tenant_id, cycle_id, subject_user_id
    )
    if snapshot is None:
        raise RecommendationNotFoundError()

    # Fetch user and benchmark in parallel.
    subject, benchmark = await asyncio.gather(
        user_repository.get_by_id_tenant_scoped(db, tenant_id, subject_user_id),
        market_benchmark_repository.get_for_subject(db, tenant_id, subject_user_id),
    )

    subject_name = f"{subject.first_name} {subject.last_name or ''}".strip() if subject else None

    current_base = snapshot.current_base
    current_variable = snapshot.current_variable
    current_tcc = _tcc(current_base, current_variable)

    rec_base = snapshot.recommended_base
    rec_variable = snapshot.recommended_variable
    rec_tcc = _tcc(rec_base, rec_variable)

    rec_increase_pct: Decimal | None = None
    if rec_base is not None and current_base:
        raw = ((rec_base - current_base) / current_base * Decimal("100")).quantize(Decimal("0.01"))
        rec_increase_pct = raw

    return JvreSnapshotResponse(
        id=snapshot.id,
        cycle_id=snapshot.cycle_id,
        subject_user_id=snapshot.subject_user_id,
        subject_name=subject_name,
        job_title=subject.job_title if subject else None,
        current_level=snapshot.recommended_level,
        compa_ratio=benchmark.compa_ratio if benchmark else None,
        current_base=current_base,
        current_variable=current_variable,
        current_tcc=current_tcc,
        current_fy_vesting_units=snapshot.current_fy_vesting_units,
        recommended_base=snapshot.recommended_base,
        recommended_variable=snapshot.recommended_variable,
        recommended_lti_fmv=snapshot.recommended_lti_fmv,
        recommended_lti_units=snapshot.recommended_lti_units,
        recommended_other_rewards=snapshot.recommended_other_rewards,
        currency_code=snapshot.currency_code,
        rec_tcc=rec_tcc,
        rec_increase_pct=rec_increase_pct,
        criticality=snapshot.criticality,
        market_position=snapshot.market_position,
        promotion_readiness=snapshot.promotion_readiness,
        recommended_level=snapshot.recommended_level,
        risk_callout_text=snapshot.risk_callout_text,
        market_gap=(benchmark.target_pay - benchmark.current_pay if benchmark else None),
        jvre_score=snapshot.jvre_score,
        ai_suggestion_text=snapshot.ai_suggestion_text,
        generated_at=snapshot.created_at,
    )


# ---------------------------------------------------------------------------
# My budget allocation (left panel)
# ---------------------------------------------------------------------------
async def get_my_budget_allocation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    owner_user_id: uuid.UUID,
) -> MyBudgetAllocationResponse:
    """Return the caller's own allocation row composed with JVRE context.

    ``current_pool_value`` is a heuristic: assumes 10% YoY growth so
    prior-FY pool ≈ total_pool / 1.10. Real ingestion lands in v0.2.
    """
    alloc = await budget_allocation_repository.get_for_owner(db, tenant_id, cycle_id, owner_user_id)
    if alloc is None:
        raise BudgetAllocationNotFoundError()

    # JVRE context.
    snapshot = await jvre_snapshot_repository.get_for_subject(
        db, tenant_id, cycle_id, owner_user_id
    )

    # Reserve band. Heuristic from owner's role tier.
    role_codes = await _user_role_codes(db, owner_user_id)
    band_min, band_max = _reserve_band_for(role_codes)
    midpoint_pct = (band_min + band_max) / 2.0
    midpoint_amount = (alloc.total_pool * Decimal(str(midpoint_pct))).quantize(Decimal("1"))
    reserve_rec = JvreReserveRecommendation(
        min_pct=band_min,
        max_pct=band_max,
        midpoint_pct=midpoint_pct,
        midpoint_amount=midpoint_amount,
    )

    # +X% over Current. v0.1 demo heuristic: assume current was the
    # next-FY pool divided by 1.10 (matches the seed's growth assumption).
    # Real ingestion in production will store the prior FY value.
    current_pool_value = (alloc.total_pool / Decimal("1.10")).quantize(Decimal("1"))
    pool_delta_vs_current_pct = float(
        (alloc.total_pool - current_pool_value) / current_pool_value * Decimal("100")
    )

    # Recommended pool for a manager = sum of recommended pay across their
    # reporting subtree (same aggregation the allocation-line ``jvre_rec_amount``
    # uses). Never the manager's own individual recommended_base. Computed
    # unconditionally so the pool appears even when the manager has no
    # personal snapshot (senior leaders often don't).
    cash_rec, _lti_rec = await _compute_jvre_pool_for(db, tenant_id, cycle_id, owner_user_id)
    jvre_recommended_pool = cash_rec
    jvre_text = _team_jvre_narrative(
        pool=alloc.total_pool,
        jvre_rec=cash_rec,
        currency=alloc.currency_code,
        reserve_band=(band_min, band_max),
    )

    # Resolve parent owner name for dashboard "Allocated by" display.
    parent_owner_name = None
    if alloc.parent_allocation_id is not None:
        parent_alloc = await budget_allocation_repository.get_for_tenant(
            db, tenant_id, alloc.parent_allocation_id
        )
        if parent_alloc is not None:
            parent_user = await user_repository.get_by_id_tenant_scoped(
                db, tenant_id, parent_alloc.owner_user_id
            )
            if parent_user is not None:
                parent_owner_name = f"{parent_user.first_name} {parent_user.last_name}".strip()

    return MyBudgetAllocationResponse(
        id=alloc.id,
        cycle_id=alloc.cycle_id,
        owner_user_id=alloc.owner_user_id,
        parent_allocation_id=alloc.parent_allocation_id,
        total_pool=alloc.total_pool,
        strategic_reserve=alloc.strategic_reserve,
        budget_for_allocation=alloc.budget_for_allocation,
        currency_code=alloc.currency_code,
        status=alloc.status,
        submitted_at=alloc.submitted_at,
        created_at=alloc.created_at,
        parent_owner_name=parent_owner_name,
        current_pool_value=current_pool_value,
        pool_delta_vs_current_pct=pool_delta_vs_current_pct,
        jvre_recommended_pool=jvre_recommended_pool,
        jvre_engine_recommends_text=jvre_text,
        jvre_reserve=reserve_rec,
    )


# ---------------------------------------------------------------------------
# Allocation lines (right panel — recipient cards)
# ---------------------------------------------------------------------------
async def list_allocation_lines(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    allocation_id: uuid.UUID,
    caller_user_id: uuid.UUID,
) -> list[BudgetAllocationLineResponse]:
    """List the lines on a specific budget allocation.

    Caller must be the allocation's owner (the actor whose Budget
    Planner this represents). Tenant-scoping is enforced upstream by
    the scoped DB session.
    """
    alloc = await budget_allocation_repository.get_for_tenant(db, tenant_id, allocation_id)
    if alloc is None:
        raise BudgetAllocationNotFoundError()
    if alloc.owner_user_id != caller_user_id:
        raise NotAllocationOwnerError()

    lines = await budget_allocation_repository.list_lines_for_allocation(db, allocation_id)
    if not lines:
        return []

    # Bulk-fetch JVRE snapshots for every recipient so chips render
    # without N+1 queries.
    recipient_ids = [line.recipient_user_id for line in lines]
    snapshots = await jvre_snapshot_repository.list_for_subjects(
        db, tenant_id, alloc.cycle_id, recipient_ids
    )
    snapshot_by_subject = {s.subject_user_id: s for s in snapshots}

    benchmarks = await market_benchmark_repository.get_for_subjects(db, tenant_id, recipient_ids)
    benchmark_by_subject = {b.subject_user_id: b for b in benchmarks}

    # Explicit tenant filter — users table has no RLS policy.
    user_by_id = await user_repository.batch_by_ids_tenant_scoped(
        db, tenant_id, recipient_ids
    )

    # Batch fetch team sizes — one query for all recipients instead of N.
    reports_by_recipient = await reporting_relationship_repository.report_ids_by_manager(
        db, tenant_id, alloc.cycle_id, recipient_ids
    )
    team_sizes = {rid: len(ids) for rid, ids in reports_by_recipient.items()}

    # Prior-cycle current pool per recipient, computed on the **full
    # subtree** (matching how ``jvre_rec_amount`` and ``allocated_amount``
    # are scoped). Using direct-reports-only here was the cause of the
    # "+940% over Current" nonsense on senior MoMs' allocation lines.
    current_pools: dict[uuid.UUID, Decimal] = {}
    for rid in recipient_ids:
        current_pools[rid] = await _compute_current_pool_for(
            db, tenant_id, alloc.cycle_id, rid
        )

    out: list[BudgetAllocationLineResponse] = []
    for line in lines:
        snapshot = snapshot_by_subject.get(line.recipient_user_id)
        benchmark = benchmark_by_subject.get(line.recipient_user_id)
        user = user_by_id.get(line.recipient_user_id)
        out.append(
            _line_to_response(
                line, snapshot, benchmark, user,
                team_sizes.get(line.recipient_user_id),
                current_pools.get(line.recipient_user_id),
                alloc.strategic_reserve,
                alloc.budget_for_allocation,
            )
        )
    return out


async def get_team_risk_snapshot(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    allocation_id: uuid.UUID,
    caller_user_id: uuid.UUID,
) -> TeamRiskSnapshotResponse:
    """Aggregate IC-level risk data for the MoM dashboard Section 1."""
    # 1. Verify allocation belongs to caller
    alloc = await budget_allocation_repository.get_for_tenant(db, tenant_id, allocation_id)
    if alloc is None:
        raise BudgetAllocationNotFoundError()
    if alloc.owner_user_id != caller_user_id:
        raise NotAllocationOwnerError()

    # 2. Get all lines (managers under this MoM)
    lines = await budget_allocation_repository.list_lines_for_allocation(db, allocation_id)

    # 3. Bulk-fetch manager users + their IC subtrees in two queries.
    mgr_ids = [line.recipient_user_id for line in lines]
    mgr_user_by_id = await user_repository.batch_by_ids_tenant_scoped(
        db, tenant_id, mgr_ids
    )

    reports_by_mgr = await reporting_relationship_repository.report_ids_by_manager(
        db, tenant_id, alloc.cycle_id, mgr_ids
    )

    # Manager info: mgr_id -> (name, department_id, [ic_ids])
    manager_info: dict[uuid.UUID, tuple[str, uuid.UUID | None, list[uuid.UUID]]] = {}
    for line in lines:
        mgr_user = mgr_user_by_id.get(line.recipient_user_id)
        if mgr_user is None:
            # Cross-tenant or missing user — skip the line. Defensive only;
            # the FK on budget_allocation_lines + tenant scoping above
            # should make this unreachable in practice.
            continue
        last = f" {mgr_user.last_name}" if mgr_user.last_name else ""
        mgr_name = f"{mgr_user.first_name}{last}".strip()
        manager_info[line.recipient_user_id] = (
            mgr_name,
            mgr_user.department_id,
            reports_by_mgr.get(line.recipient_user_id, []),
        )

    # 4. Collect all IC ids
    all_ic_ids = [ic for _, _, ics in manager_info.values() for ic in ics]
    if not all_ic_ids:
        return TeamRiskSnapshotResponse(
            summary=[],
            breakdown_below_market=0,
            breakdown_promotion_eligible=0,
            breakdown_critical_talent=0,
            below_market_groups=[],
            promotion_eligible=[],
            critical_talent=[],
            all_members_by_manager=[],
        )

    # 5. Bulk fetch JVRE snapshots and market benchmarks for all ICs
    snapshots = await jvre_snapshot_repository.list_for_subjects(
        db, tenant_id, alloc.cycle_id, all_ic_ids
    )
    snapshot_by_ic = {s.subject_user_id: s for s in snapshots}

    benchmarks = await market_benchmark_repository.get_for_subjects(db, tenant_id, all_ic_ids)
    benchmark_by_ic = {b.subject_user_id: b for b in benchmarks}

    # 5b. Bulk-fetch IC User rows (tenant-scoped, single query).
    ic_user_by_id = await user_repository.batch_by_ids_tenant_scoped(
        db, tenant_id, all_ic_ids
    )

    # 5c. Bulk-fetch Department names for every department_id seen on
    #     either a manager or an IC. One query covers both groups.
    dept_ids: set[uuid.UUID] = set()
    for _, mgr_dept_id, _ in manager_info.values():
        if mgr_dept_id is not None:
            dept_ids.add(mgr_dept_id)
    for ic_user in ic_user_by_id.values():
        if ic_user.department_id is not None:
            dept_ids.add(ic_user.department_id)
    dept_name_by_id: dict[uuid.UUID, str] = {}
    if dept_ids:
        dept_rows = (
            (
                await db.execute(
                    select(Department).where(
                        Department.id.in_(dept_ids),
                        Department.tenant_id == tenant_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        dept_name_by_id = {d.id: d.name for d in dept_rows}

    # 6. Aggregate risk data
    critical_count = 0
    moderate_high_count = 0
    low_risk_count = 0
    below_market_groups: list[RiskSnapshotGroup] = []
    promotion_eligible: list[RiskSnapshotPromoMember] = []
    critical_talent: list[RiskSnapshotCriticalMember] = []
    all_members_by_manager: list[RiskSnapshotManagerGroup] = []

    for _mgr_id, (mgr_name, mgr_dept_id, ic_ids) in manager_info.items():
        mgr_dept = dept_name_by_id.get(mgr_dept_id) if mgr_dept_id else None
        group_members: list[RiskSnapshotMember] = []
        all_group_members: list[RiskSnapshotAllMember] = []

        for ic_id in ic_ids:
            snapshot = snapshot_by_ic.get(ic_id)
            benchmark = benchmark_by_ic.get(ic_id)
            ic_user = ic_user_by_id.get(ic_id)
            if not ic_user:
                continue
            ic_name = f"{ic_user.first_name} {ic_user.last_name or ''}".strip()

            # Risk level counts
            crit = (snapshot.criticality or "").upper() if snapshot else ""
            if crit == "CRITICAL":
                critical_count += 1
            elif crit == "MODERATE_HIGH":
                moderate_high_count += 1
            else:
                low_risk_count += 1

            # Below Market
            mkt = (snapshot.market_position or "").upper() if snapshot else ""
            if mkt == "BELOW_MARKET" and benchmark:
                market_gap = benchmark.target_pay - benchmark.current_pay
                group_members.append(
                    RiskSnapshotMember(
                        subject_user_id=ic_id,
                        subject_name=ic_name,
                        manager_name=mgr_name,
                        manager_department=mgr_dept,
                        market_gap=market_gap,
                    )
                )

            # Promotion Eligible
            promo = (snapshot.promotion_readiness or "").upper() if snapshot else ""
            if promo in ("READY", "CANDIDATE"):
                promotion_eligible.append(
                    RiskSnapshotPromoMember(
                        subject_user_id=ic_id,
                        subject_name=ic_name,
                        manager_name=mgr_name,
                        # current_level needs to come from a source that
                        # actually holds the subject's CURRENT level (e.g.
                        # compensation_history or a new column on users) —
                        # aliasing it to recommended_level would falsely
                        # present a "L5 → L5" callout. None until wired.
                        current_level=None,
                        recommended_level=snapshot.recommended_level if snapshot else None,
                    )
                )

            # Critical Talent
            if crit == "CRITICAL" and snapshot and snapshot.risk_callout_text:
                critical_talent.append(
                    RiskSnapshotCriticalMember(
                        subject_user_id=ic_id,
                        subject_name=ic_name,
                        manager_name=mgr_name,
                        risk_callout_text=snapshot.risk_callout_text,
                    )
                )
            # Collect all members with compa ratio for the full-roster panel.
            all_group_members.append(
                RiskSnapshotAllMember(
                    subject_user_id=ic_id,
                    subject_name=ic_name,
                    current_level=snapshot.recommended_level if snapshot else None,
                    compa_ratio=benchmark.compa_ratio if benchmark else None,
                    market_position=snapshot.market_position if snapshot else None,
                    criticality=snapshot.criticality if snapshot else None,
                    job_title=ic_user.job_title if ic_user else None,
                ),
            )
        all_members_by_manager.append(
            RiskSnapshotManagerGroup(
                manager_name=mgr_name,
                manager_department=mgr_dept,
                members=all_group_members,
            )
        )

        if group_members:
            below_market_groups.append(
                RiskSnapshotGroup(
                    manager_name=mgr_name,
                    manager_department=mgr_dept,
                    members=group_members,
                )
            )

    summary = []
    if critical_count:
        summary.append(RiskSummaryItem(level="Critical", count=critical_count))
    if moderate_high_count:
        summary.append(RiskSummaryItem(level="Moderate-High", count=moderate_high_count))
    if low_risk_count:
        summary.append(RiskSummaryItem(level="Low Risk", count=low_risk_count))

    return TeamRiskSnapshotResponse(
        summary=summary,
        breakdown_below_market=len([m for g in below_market_groups for m in g.members]),
        breakdown_promotion_eligible=len(promotion_eligible),
        breakdown_critical_talent=len(critical_talent),
        below_market_groups=below_market_groups,
        promotion_eligible=promotion_eligible,
        critical_talent=critical_talent,
        all_members_by_manager=all_members_by_manager,
    )


def _line_to_response(
    line: BudgetAllocationLine,
    snapshot: JvreSnapshot | None,
    benchmark: object | None,
    user: User | None,
    team_size: int | None,
    current_pool: Decimal | None = None,
    strategic_reserve: Decimal | None = None,
    budget_for_allocation: Decimal | None = None,
) -> BudgetAllocationLineResponse:
    """Pure sync helper — all data pre-fetched by the caller in bulk.

    current_pool: prior-FY pool sum for the recipient's subtree (for the
    "+X% vs current" chip). strategic_reserve + budget_for_allocation are
    needed to compute the proportional JVRE pool breakdown shown on the card.
    """
    if user:
        last = f" {user.last_name}" if user.last_name else ""
        name = f"{user.first_name}{last}"
    else:
        name = ""

    # Proportional JVRE pool breakdown — same formula used at align-time.
    jvre_base_pool: Decimal | None = None
    jvre_variable_pool: Decimal | None = None
    jvre_reserve_pool: Decimal | None = None
    if strategic_reserve is not None and budget_for_allocation and budget_for_allocation > 0:
        _jvre_reserve = _round_money(
            strategic_reserve * line.jvre_rec_amount / budget_for_allocation
        )
        _jvre_cash = max(
            line.jvre_rec_amount - line.lti_grant_fmv_pool - _jvre_reserve, Decimal("0")
        )
        jvre_base_pool, jvre_variable_pool = _split_cash(_jvre_cash)
        jvre_reserve_pool = _jvre_reserve

    return BudgetAllocationLineResponse(
        id=line.id,
        allocation_id=line.allocation_id,
        recipient_user_id=line.recipient_user_id,
        recipient_name=name,
        recipient_department=user.department.name if user and user.department else None,
        recipient_team_size=team_size,
        allocated_amount=line.allocated_amount,
        base_pool=line.base_pool,
        variable_pool=line.variable_pool,
        lti_grant_fmv_pool=line.lti_grant_fmv_pool,
        reserve_pool=line.reserve_pool,
        jvre_rec_amount=line.jvre_rec_amount,
        currency_code=line.currency_code,
        notes=line.notes,
        current_pool=current_pool,
        jvre_base_pool=jvre_base_pool,
        jvre_variable_pool=jvre_variable_pool,
        jvre_reserve_pool=jvre_reserve_pool,
        criticality=snapshot.criticality if snapshot else None,
        market_position=snapshot.market_position if snapshot else None,
        promotion_readiness=snapshot.promotion_readiness if snapshot else None,
        risk_callout_text=snapshot.risk_callout_text if snapshot else None,
        ai_suggestion_text=snapshot.ai_suggestion_text if snapshot else None,
        compa_ratio=benchmark.compa_ratio if benchmark else None,
        # current_level needs its own source (e.g. compensation_history or
        # a new column on users) — aliasing recommended_level here would
        # falsely present "L5 → L5" callouts. None until properly wired.
        current_level=None,
        market_gap=benchmark.target_pay - benchmark.current_pay if benchmark else None,
        recommended_level=snapshot.recommended_level if snapshot else None,
        job_title=user.job_title if user else None,
        comp_base_pay=snapshot.recommended_base if snapshot else None,
        comp_variable_pay=snapshot.recommended_variable if snapshot else None,
        comp_lti_fmv=snapshot.recommended_lti_fmv if snapshot else None,
        comp_other_rewards=snapshot.recommended_other_rewards if snapshot else None,
    )


def _aggregate_rec_totals(
    recs: list,
    comps_by_rec: dict,
) -> tuple[dict, dict, dict]:
    component_totals: dict[uuid.UUID, Decimal] = {}
    mgr_rec_totals: dict[uuid.UUID, Decimal] = {}
    mom_rec_totals: dict[uuid.UUID, Decimal] = {}
    for rec in recs:
        comps = comps_by_rec.get(rec.id, [])
        component_totals[rec.id] = _sum_total_rewards(comps)
        mgr_val = _sum_mgr_rec(comps)
        mom_val = _sum_mom_rec(comps)
        if mgr_val is not None:
            mgr_rec_totals[rec.id] = mgr_val
        if mom_val is not None:
            mom_rec_totals[rec.id] = mom_val
    return component_totals, mgr_rec_totals, mom_rec_totals


# ---------------------------------------------------------------------------
# My recommendations list (the MoP's / MoM's per-card grid)
# ---------------------------------------------------------------------------
async def list_my_recommendation_subjects(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> list[MyRecommendationSubjectResponse]:
    """Subjects the actor is responsible for in this cycle.

    Subjects = the actor's direct reports per
    ``reporting_relationships``. For each subject we attach:

    * The recommendation row if it exists (status, totals).
    * The JVRE snapshot for chips + JVRE Rec total.
    * A ``status`` string of ``PENDING`` when no recommendation row
      exists yet (frontend creates it on first edit).
    """
    report_ids = await reporting_relationship_repository.report_ids(
        db, tenant_id, cycle_id, actor_user_id
    )
    if not report_ids:
        return []

    # Existing recommendations for the actor in this cycle.
    recs = await pay_recommendation_repository.list_for_actor(
        db, tenant_id, cycle_id, actor_user_id
    )
    rec_by_subject = {r.subject_user_id: r for r in recs}

    # Bulk Fetch
    snapshot_by_subject, benchmark_by_subject, user_by_id = await _bulk_fetch_subject_data(
        db, tenant_id, cycle_id, report_ids
    )

    # Batch-fetch all components for all recs in one query.
    comps_by_rec = await pay_recommendation_repository.list_components_batch(
        db, [r.id for r in recs]
    )

    # Component aggregates per existing recommendation.
    component_totals, mgr_rec_totals, mom_rec_totals = _aggregate_rec_totals(
        recs, comps_by_rec
    )

    out: list[MyRecommendationSubjectResponse] = []
    for subject_id in report_ids:
        _k = str(uuid.UUID(str(subject_id)))
        rec = rec_by_subject.get(subject_id)
        subject = user_by_id.get(_k)
        benchmark = benchmark_by_subject.get(_k)
        snapshot = snapshot_by_subject.get(_k)
        jvre_total = _jvre_total(snapshot)
        final_total = component_totals.get(rec.id) if rec is not None else None

        deviation_pct = (
            float((final_total - jvre_total) / jvre_total * Decimal("100"))
            if final_total is not None and jvre_total
            else None
        )

        out.append(
            _build_subject_response(
                subject_id=subject_id,
                subject=subject,
                snapshot=snapshot,
                benchmark=benchmark,
                rec=rec,
                final_total=final_total,
                mgr_total=mgr_rec_totals.get(rec.id) if rec else None,
                mom_total=mom_rec_totals.get(rec.id) if rec else None,
                jvre_total=jvre_total,
                deviation_pct=deviation_pct,
            )
        )
    return out


def _jvre_total(snapshot: JvreSnapshot | None) -> Decimal | None:
    if snapshot is None:
        return None
    parts = [
        snapshot.recommended_base or Decimal("0"),
        snapshot.recommended_variable or Decimal("0"),
        snapshot.recommended_lti_fmv or Decimal("0"),
        snapshot.recommended_other_rewards or Decimal("0"),
    ]
    total = sum(parts, Decimal("0"))
    return total if total > 0 else None


def _sum_total_rewards(
    components: list[PayRecommendationComponent],
) -> Decimal | None:
    """Sum BASE + VARIABLE + LTI_GRANT_FMV + OTHER_REWARDS final_values."""
    total = Decimal("0")
    seen_any = False
    for comp in components:
        if comp.component == "LTI_UNITS":
            continue
        val = _final_value(comp)
        if val is not None:
            total += val
            seen_any = True
    return total if seen_any else None


def _sum_mgr_rec(components: list[PayRecommendationComponent]) -> Decimal | None:
    """Sum mgr_rec_value across BASE + VARIABLE + LTI_GRANT_FMV + OTHER_REWARDS."""
    total = Decimal("0")
    seen_any = False
    for comp in components:
        if comp.component == "LTI_UNITS":
            continue
        val = comp.mgr_rec_value
        if val is not None:
            total += val
            seen_any = True
    return total if seen_any else None


def _sum_mom_rec(components: list[PayRecommendationComponent]) -> Decimal | None:
    """Sum mom_rec_value across BASE + VARIABLE + LTI_GRANT_FMV + OTHER_REWARDS."""
    total = Decimal("0")
    seen_any = False
    for comp in components:
        if comp.component == "LTI_UNITS":
            continue
        val = comp.mom_rec_value
        if val is not None:
            total += val
            seen_any = True
    return total if seen_any else None


# ---------------------------------------------------------------------------
# Single recommendation snapshot
# ---------------------------------------------------------------------------
async def get_recommendation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    recommendation_id: uuid.UUID,
    caller_user_id: uuid.UUID,
) -> PayRecommendationResponse:
    rec = await pay_recommendation_repository.get_for_tenant(db, tenant_id, recommendation_id)
    if rec is None:
        raise RecommendationNotFoundError()

    # The caller must be either the actor (their own recommendation)
    # or upstream of the actor in the reporting chain (the MoM
    # reviewing an MoP's submission).
    if not await _caller_can_read_recommendation(db, tenant_id, rec, caller_user_id):
        raise SubjectNotInReportingChainError()

    components = await pay_recommendation_repository.list_components(db, rec.id)
    override = await pay_recommendation_repository.get_override(db, rec.id, rec.actor_user_id)
    annotations = await pay_recommendation_repository.list_annotations(db, rec.id)
    snapshot = await jvre_snapshot_repository.get_for_subject(
        db, tenant_id, rec.cycle_id, rec.subject_user_id
    )

    subject_user = await user_repository.get_by_id_tenant_scoped(
        db, tenant_id, rec.subject_user_id, load_department=True
    )
    subject_name = (
        f"{subject_user.first_name} {subject_user.last_name or ''}".strip()
        if subject_user
        else await _user_name(db, rec.subject_user_id)
    )
    annotation_responses = []
    for ann in annotations:
        annotation_responses.append(await _annotation_to_response(db, ann))
    return PayRecommendationResponse(
        id=rec.id,
        cycle_id=rec.cycle_id,
        actor_user_id=rec.actor_user_id,
        subject_user_id=rec.subject_user_id,
        subject_name=subject_name,
        subject_level=snapshot.recommended_level if snapshot else None,
        subject_department=(
            subject_user.department.name if subject_user and subject_user.department else None
        ),
        parent_recommendation_id=rec.parent_recommendation_id,
        relationship_kind=rec.relationship_kind,
        status=rec.status,
        submitted_at=rec.submitted_at,
        approved_at=rec.approved_at,
        currency_code=rec.currency_code,
        components=[_component_to_response(c) for c in components],
        override=_override_to_response(override) if override else None,
        annotations=annotation_responses,
        jvre_snapshot=(
            await build_jvre_snapshot_response(db, tenant_id, rec.cycle_id, rec.subject_user_id)
            if snapshot
            else None
        ),
    )


async def _caller_can_read_recommendation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    rec: PayRecommendation,
    caller_user_id: uuid.UUID,
) -> bool:
    """Caller is allowed to read if:

    * They authored the recommendation, OR
    * They're in the upstream reporting chain (i.e. the actor reports
      to them in the same cycle).
    """
    if rec.actor_user_id == caller_user_id:
        return True
    actor_mgr = await reporting_relationship_repository.get_manager_of(
        db, tenant_id, rec.cycle_id, rec.actor_user_id
    )
    return actor_mgr is not None and actor_mgr.manager_user_id == caller_user_id


def _override_to_response(
    override: PayRecommendationOverride,
) -> PayRecommendationOverrideResponse:
    return PayRecommendationOverrideResponse(
        actor_user_id=override.actor_user_id,
        reason_code=override.reason_code,
        role_criticality=override.role_criticality,
        promotion_consideration=override.promotion_consideration,
        created_at=override.created_at,
    )


async def _annotation_to_response(
    db: AsyncSession, annotation: PayRecommendationAnnotation
) -> PayRecommendationAnnotationResponse:
    return PayRecommendationAnnotationResponse(
        id=annotation.id,
        actor_user_id=annotation.actor_user_id,
        actor_name=await _user_name(db, annotation.actor_user_id),
        text=annotation.text,
        created_at=annotation.created_at,
    )


# ---------------------------------------------------------------------------
# Pending review (MoM's tabbed review screen)
# ---------------------------------------------------------------------------
async def list_pending_review(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    reviewer_user_id: uuid.UUID,
) -> PendingReviewResponse:
    """Submissions awaiting the caller's approval, grouped by submitter."""
    # The MoM's submitters are their direct-report MoPs.
    submitter_ids = await reporting_relationship_repository.report_ids(
        db, tenant_id, cycle_id, reviewer_user_id
    )

    rows = await pay_recommendation_repository.list_submissions_for_actors(
        db, tenant_id, cycle_id, submitter_ids
    )

    # Group by submitter.
    by_submitter: dict[uuid.UUID, list[PayRecommendation]] = {}
    for row in rows:
        by_submitter.setdefault(row.actor_user_id, []).append(row)

    # Bulk fetch submitter users with department — avoids N+1.
    # Explicit tenant scope required: users table has no RLS policy.
    submitters_map = await user_repository.batch_by_ids_tenant_scoped(
        db, tenant_id, submitter_ids, load_department=True
    )
    submitter_by_id: dict[str, User] = {str(uid): u for uid, u in submitters_map.items()}

    submitters: list[PendingReviewSubmitter] = []
    for submitter_id in submitter_ids:
        submissions = by_submitter.get(submitter_id, [])
        member_subjects = await _submissions_to_summary(db, tenant_id, cycle_id, submissions)
        submitter = submitter_by_id.get(str(submitter_id))
        submitters.append(
            PendingReviewSubmitter(
                submitter_user_id=submitter_id,
                submitter_name=(
                    f"{submitter.first_name} {submitter.last_name or ''}".strip()
                    if submitter
                    else ""
                ),
                submitter_department=(
                    submitter.department.name if submitter and submitter.department else None
                ),
                member_count=len(member_subjects),
                review_status=_roll_up_review_status(submissions),
                members=member_subjects,
            )
        )
    return PendingReviewResponse(cycle_id=cycle_id, submitters=submitters)


async def _submissions_to_summary(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    submissions: list[PayRecommendation],
) -> list[MyRecommendationSubjectResponse]:
    """Convert a flat list of submitted recommendations into the same
    summary shape used by the MoP's recommendations-list endpoint, so
    the frontend's row component is reusable across both screens."""
    if not submissions:
        return []

    subject_ids = [r.subject_user_id for r in submissions]
    snapshot_by_subject, benchmark_by_subject, user_by_id = await _bulk_fetch_subject_data(
        db, tenant_id, cycle_id, subject_ids
    )

    # Batch-fetch all components for all submissions in one query.
    comps_by_rec = await pay_recommendation_repository.list_components_batch(
        db, [r.id for r in submissions]
    )

    out: list[MyRecommendationSubjectResponse] = []
    for rec in submissions:
        _key = str(uuid.UUID(str(rec.subject_user_id)))
        snapshot = snapshot_by_subject.get(_key)
        benchmark = benchmark_by_subject.get(_key)
        subject = user_by_id.get(_key)
        components = comps_by_rec.get(rec.id, [])
        final_total = _sum_total_rewards(components)
        mgr_total = _sum_mgr_rec(components)
        mom_total = _sum_mom_rec(components)
        jvre_total = _jvre_total(snapshot)
        deviation_pct = (
            float((final_total - jvre_total) / jvre_total * Decimal("100"))
            if final_total is not None and jvre_total
            else None
        )

        out.append(
            _build_subject_response(
                subject_id=rec.subject_user_id,
                subject=subject,
                snapshot=snapshot,
                benchmark=benchmark,
                rec=rec,
                final_total=final_total,
                mgr_total=mgr_total,
                mom_total=mom_total,
                jvre_total=jvre_total,
                deviation_pct=deviation_pct,
            )
        )
    return out


def _roll_up_review_status(submissions: list[PayRecommendation]) -> str:
    """Map a tab's review state for the screen header chip.

    Precedence: all-APPROVED → COMPLETED; any UNDER_REVIEW → IN_REVIEW;
    otherwise PENDING.
    """
    if not submissions:
        return "PENDING"
    statuses = {r.status for r in submissions}
    if statuses == {"APPROVED"}:
        return "COMPLETED"
    if "UNDER_REVIEW" in statuses:
        return "IN_REVIEW"
    return "PENDING"


# ---------------------------------------------------------------------------
# Reporting-chain check (used by reference-data endpoints)
# ---------------------------------------------------------------------------
async def assert_subject_in_reporting_chain(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    caller_user_id: uuid.UUID,
    subject_user_id: uuid.UUID,
) -> None:
    """Caller must be the subject themselves OR the subject's manager
    (or the manager's manager — for v0.1 we walk up to two levels,
    matching the MoM-can-see-IC-rec pattern). Raise on failure.

    A clean ``require_in_reporting_chain`` dependency lands in Phase 7;
    for v0.1 the inline check covers the API surface.
    """
    if caller_user_id == subject_user_id:
        return

    # Need an active cycle to look up reporting relationships.
    cycle = await compensation_cycle_repository.get_active(db, tenant_id)
    if cycle is None:
        raise SubjectNotInReportingChainError()

    # Direct manager of subject?
    direct = await reporting_relationship_repository.get_manager_of(
        db, tenant_id, cycle.id, subject_user_id
    )
    if direct is not None and direct.manager_user_id == caller_user_id:
        return

    # Manager's manager (covers MoM looking at an IC).
    if direct is not None:
        upstream = await reporting_relationship_repository.get_manager_of(
            db, tenant_id, cycle.id, direct.manager_user_id
        )
        if upstream is not None and upstream.manager_user_id == caller_user_id:
            return

    raise SubjectNotInReportingChainError()


# ---------------------------------------------------------------------------
# Write-side: MoM Budget Allocation (Phase 4)
# ---------------------------------------------------------------------------
# Default per-pool split when initialize-from-JVRE happens. The MoM
# can override after initialization. Adds to 1.00; the reserve_pool is
# the remainder so floating-point round-off doesn't drift the totals.
_DEFAULT_BASE_RATIO = Decimal("0.65")
_DEFAULT_VARIABLE_RATIO = Decimal("0.20")
# Within the cash pool (base + variable only), base takes 65/(65+20).
_BASE_IN_CASH_RATIO = _DEFAULT_BASE_RATIO / (_DEFAULT_BASE_RATIO + _DEFAULT_VARIABLE_RATIO)


def _round_money(amount: Decimal) -> Decimal:
    """Snap to two decimals (matches NUMERIC(18, 2) on disk)."""
    return amount.quantize(Decimal("0.01"))


# NOTE: The runtime FX helper is no longer needed. Every monetary value in
# the tenant's transactional layer (jvre_snapshots, market_benchmarks,
# iquest_engine_output, compensation_history, budget_allocations) is stored
# in the reporting currency (USD) — the seeder converts from each
# employee's local currency at insert time via
# ``genpact_currency_master``. Sums here can be raw. If a future tenant
# switches its reporting currency, the seeder needs updating — this
# service layer does not.


def _split_cash(cash: Decimal) -> tuple[Decimal, Decimal]:
    """Split a cash pool (base + variable, no LTI, no reserve) into
    (base, variable). Variable absorbs rounding drift."""
    base = _round_money(cash * _BASE_IN_CASH_RATIO)
    variable = _round_money(cash - base)
    return base, variable


async def _get_owned_allocation_or_raise(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    allocation_id: uuid.UUID,
    caller_user_id: uuid.UUID,
    *,
    require_pending: bool = True,
) -> BudgetAllocation:
    """Fetch + validate caller is the owner + optionally require PENDING."""
    alloc = await budget_allocation_repository.get_for_tenant(db, tenant_id, allocation_id)
    if alloc is None:
        raise BudgetAllocationNotFoundError()
    if alloc.owner_user_id != caller_user_id:
        raise NotAllocationOwnerError()
    if require_pending and alloc.status != BudgetAllocationStatus.PENDING.value:
        raise BudgetAllocationNotEditableError(alloc.status)
    return alloc


async def _collect_subtree_user_ids(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    root_user_id: uuid.UUID,
) -> set[uuid.UUID]:
    """Every user_id in the reporting subtree rooted at ``root_user_id``,
    INCLUDING the root.

    Walks down ``reporting_relationships`` for the cycle. For our v0.1
    org tree (CFO → MoM → MoP → IC) the depth is at most 3. Pure
    BFS without recursion so the SQL stays simple.
    """
    visited: set[uuid.UUID] = {root_user_id}
    frontier: list[uuid.UUID] = [root_user_id]
    while frontier:
        next_frontier: list[uuid.UUID] = []
        for uid in frontier:
            children = await reporting_relationship_repository.report_ids(
                db, tenant_id, cycle_id, uid
            )
            for child_id in children:
                if child_id not in visited:
                    visited.add(child_id)
                    next_frontier.append(child_id)
        frontier = next_frontier
    return visited


async def _compute_jvre_pool_for(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    recipient_user_id: uuid.UUID,
) -> tuple[Decimal, Decimal]:
    """Return ``(cash_rec, lti_rec)`` for the recipient's reporting subtree.

    ``cash_rec`` = sum of recommended_base + recommended_variable +
    recommended_other_rewards across the subtree. This is the dynamic
    budget-pool component that flows through the MoM → MoP cascade.

    ``lti_rec`` = sum of recommended_lti_fmv across the subtree. LTI is
    a static grant determined by the board / C-suite and is NOT part of
    the distributable cash pool — it is tracked on the line for visibility
    but does not participate in reserve proportionality calculations.

    A single subtree walk + one bulk snapshot fetch; no N+1 inside.
    """
    subtree = await _collect_subtree_user_ids(db, tenant_id, cycle_id, recipient_user_id)
    snapshots = await jvre_snapshot_repository.list_for_subjects(
        db, tenant_id, cycle_id, list(subtree)
    )
    # Every stored snapshot is already in the reporting currency (USD);
    # sum straight through — no FX conversion at runtime.
    cash = Decimal("0")
    lti = Decimal("0")
    for snap in snapshots:
        cash += snap.recommended_base or Decimal("0")
        cash += snap.recommended_variable or Decimal("0")
        cash += snap.recommended_other_rewards or Decimal("0")
        lti += snap.recommended_lti_fmv or Decimal("0")
    return _round_money(cash), _round_money(lti)


async def _compute_current_pool_for(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    recipient_user_id: uuid.UUID,
) -> Decimal:
    """Prior-cycle cash pool for the recipient's full reporting subtree.

    Must use the **same subtree scope** as :func:`_compute_jvre_pool_for`
    so the "vs Current" delta on each budget line is meaningful. The
    previous implementation summed only direct reports' current pay,
    which — against an allocated_amount computed over the full subtree —
    produced nonsense percentages like "+940% over Current" for a
    senior MoM whose direct reports each own big teams of their own.
    All snapshot values are pre-stored in the reporting currency (USD),
    so this is a straight sum with no runtime FX.
    """
    subtree = await _collect_subtree_user_ids(db, tenant_id, cycle_id, recipient_user_id)
    snapshots = await jvre_snapshot_repository.list_for_subjects(
        db, tenant_id, cycle_id, list(subtree)
    )
    total = Decimal("0")
    for snap in snapshots:
        total += snap.current_base or Decimal("0")
        total += snap.current_variable or Decimal("0")
    return _round_money(total)


def _fmt_money(amount: Decimal, currency: str) -> str:
    """Compact currency label for use in narrative text.

    The tenant's reporting currency is USD, so the default branch handles
    ``$`` with K/M/B suffixes. Legacy branches for INR (crore/lakh) and
    other locals are kept for defensive rendering — if a stray non-USD
    row reaches here it still formats sensibly.
    """
    val = float(amount or 0)
    sym = {"USD": "$", "INR": "₹", "PLN": "zł", "PHP": "₱", "MXN": "$"}.get(currency, f"{currency} ")
    if currency == "INR":
        crore = 10_000_000
        lakh = 100_000
        if abs(val) >= crore:
            return f"{sym}{val / crore:.2f} crore"
        if abs(val) >= lakh:
            return f"{sym}{val / lakh:.2f} lakh"
        return f"{sym}{int(round(val))}"
    if abs(val) >= 1_000_000_000:
        return f"{sym}{val / 1_000_000_000:.2f}B"
    if abs(val) >= 1_000_000:
        return f"{sym}{val / 1_000_000:.2f}M"
    if abs(val) >= 10_000:
        return f"{sym}{val / 1_000:.1f}K"
    return f"{sym}{int(round(val))}"


def _team_jvre_narrative(
    *,
    pool: Decimal,
    jvre_rec: Decimal,
    currency: str,
    reserve_band: tuple[float, float],
) -> str:
    """One-line team-level narrative for the Budget Planner card.

    Replaces the previous behaviour of surfacing the manager's *personal*
    ``ai_suggestion_text`` at team scope — which read as nonsense (e.g.
    "Recommended +1% to sustain market alignment" on a ₹122 crore pool).
    Amounts render via :func:`_fmt_money` for compact, currency-appropriate
    display (crore/lakh for INR, K/M/B for others).
    """
    pool_txt = _fmt_money(pool, currency)
    rec_txt = _fmt_money(jvre_rec, currency)
    if not pool:
        return (
            f"JVRE recommends a team pool of {rec_txt} for this cycle "
            "(no current pool to compare against yet)."
        )
    delta_pct = int(round(float((jvre_rec - pool) / pool * 100))) if pool else 0
    lo, hi = int(reserve_band[0] * 100), int(reserve_band[1] * 100)
    if jvre_rec > pool:
        direction = (
            f"The JVRE recommendation ({rec_txt}) sits about {abs(delta_pct)}% "
            f"above the current pool ({pool_txt}) — expect trade-offs across your reports."
        )
    elif jvre_rec < pool * Decimal("0.95"):
        direction = (
            f"The JVRE recommendation ({rec_txt}) is about {abs(delta_pct)}% "
            f"below the current pool ({pool_txt}), so there's headroom for retention "
            "interventions and off-cycle adjustments."
        )
    else:
        direction = (
            f"The JVRE recommendation ({rec_txt}) is well matched to your pool ({pool_txt})."
        )
    return f"{direction} A strategic reserve of {lo}–{hi}% is recommended for your role tier."


# ---- PUT /budget-allocations/{id} -----------------------------------------
async def update_budget_allocation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    allocation_id: uuid.UUID,
    request: BudgetAllocationUpdateRequest,
    *,
    caller_user_id: uuid.UUID,
) -> BudgetAllocation:
    """Patch the strategic reserve. Recomputes ``budget_for_allocation``."""
    alloc = await _get_owned_allocation_or_raise(db, tenant_id, allocation_id, caller_user_id)
    if request.strategic_reserve > alloc.total_pool:
        raise StrategicReserveExceedsPoolError(request.strategic_reserve, alloc.total_pool)
    alloc.strategic_reserve = request.strategic_reserve
    alloc.budget_for_allocation = alloc.total_pool - request.strategic_reserve
    await db.flush()
    await db.refresh(alloc, ["updated_at"])
    await audit_log_service.log_action(
        db,
        actor_user_id=caller_user_id,
        action="BUDGET_ALLOCATION_UPDATED",
        tenant_id=tenant_id,
        resource_type="budget_allocation",
        resource_id=str(alloc.id),
        metadata={
            "cycle_id": str(alloc.cycle_id),
            "strategic_reserve": str(alloc.strategic_reserve),
            "budget_for_allocation": str(alloc.budget_for_allocation),
        },
    )
    return alloc


# ---- POST /budget-allocations/{id}/align-with-jvre ------------------------
async def align_lines_with_jvre(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    allocation_id: uuid.UUID,
    *,
    caller_user_id: uuid.UUID,
) -> list[BudgetAllocationLine]:
    """Initialize-or-reset every line on the allocation to JVRE rec.

    Doubles as the "Allocate Budget" button's backend action: if no
    lines exist yet (the typical post-seed state for a MoM), they are
    created here from the JVRE recommendations for each direct report.
    Subsequent calls reset ALL existing line overrides back to JVRE — destructive.
    """
    alloc = await _get_owned_allocation_or_raise(db, tenant_id, allocation_id, caller_user_id)

    report_ids = await reporting_relationship_repository.report_ids(
        db, tenant_id, alloc.cycle_id, alloc.owner_user_id
    )
    existing = await budget_allocation_repository.list_lines_for_allocation(db, alloc.id)
    existing_by_recipient: dict[uuid.UUID, BudgetAllocationLine] = {
        line.recipient_user_id: line for line in existing
    }

    # Pass 1 — compute cash rec and LTI rec per recipient in one subtree walk each.
    cash_recs: dict[uuid.UUID, Decimal] = {}
    lti_recs: dict[uuid.UUID, Decimal] = {}
    for report_id in report_ids:
        cash, lti = await _compute_jvre_pool_for(db, tenant_id, alloc.cycle_id, report_id)
        cash_recs[report_id] = cash
        lti_recs[report_id] = lti

    # Pass 2 — assign proportional reserves and write lines.
    #
    # Reserve rule: share of parent's strategic_reserve = same % as share of
    # parent's budget_for_allocation. If Aleksei receives 10% of the budget
    # he gets 10% of the strategic reserve.
    #
    #   reserve_i = strategic_reserve * (jvre_total_i / budget_for_allocation)
    #
    # LTI rule: static board-determined grant — not part of the cash cascade.
    for report_id in report_ids:
        cash = cash_recs[report_id]
        lti = lti_recs[report_id]
        jvre_total = cash + lti

        reserve = (
            _round_money(alloc.strategic_reserve * jvre_total / alloc.budget_for_allocation)
            if alloc.budget_for_allocation > 0
            else Decimal("0")
        )
        # `allocated_amount` must never fall below the recipient's JVRE
        # recommendation (which is itself >= their current pay) — the manager's
        # pool is sized with headroom above the JVRE total precisely so pay
        # never has to be cut to fit. `reserve` is tracked for display only.
        base, variable = _split_cash(cash)
        allocated = base + variable + lti

        line = existing_by_recipient.get(report_id)
        if line is None:
            line = BudgetAllocationLine(
                allocation_id=alloc.id,
                recipient_user_id=report_id,
                allocated_amount=allocated,
                base_pool=base,
                variable_pool=variable,
                lti_grant_fmv_pool=lti,
                reserve_pool=reserve,
                jvre_rec_amount=jvre_total,
                currency_code=alloc.currency_code,
            )
            db.add(line)
        else:
            line.allocated_amount = allocated
            line.base_pool = base
            line.variable_pool = variable
            line.lti_grant_fmv_pool = lti
            line.reserve_pool = reserve
            line.jvre_rec_amount = jvre_total

    await db.flush()

    await audit_log_service.log_action(
        db,
        actor_user_id=caller_user_id,
        action="BUDGET_LINES_ALIGNED_WITH_JVRE",
        tenant_id=tenant_id,
        resource_type="budget_allocation",
        resource_id=str(alloc.id),
        metadata={
            "cycle_id": str(alloc.cycle_id),
            "recipient_count": len(report_ids),
            "lines_upserted": len(report_ids),
        },
    )

    return await budget_allocation_repository.list_lines_for_allocation(db, alloc.id)


# ---- PUT /budget-allocations/{id}/lines/{line_id} -------------------------
async def update_allocation_line(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    allocation_id: uuid.UUID,
    line_id: uuid.UUID,
    request: BudgetAllocationLineUpdateRequest,
    *,
    caller_user_id: uuid.UUID,
) -> BudgetAllocationLine:
    """Apply a partial patch. See request docstring for the two modes."""
    alloc = await _get_owned_allocation_or_raise(db, tenant_id, allocation_id, caller_user_id)
    line = await budget_allocation_repository.get_line(db, alloc.id, line_id)
    if line is None:
        raise BudgetAllocationLineNotFoundError()

    pool_fields = (
        request.base_pool,
        request.variable_pool,
        request.lti_grant_fmv_pool,
        request.reserve_pool,
    )
    pool_fields_provided = any(f is not None for f in pool_fields)

    if request.allocated_amount is not None and not pool_fields_provided:
        # Quick edit: reserve = same % of strategic_reserve as allocated is of
        # budget_for_allocation. LTI is static. base+variable fill the rest.
        new_allocated = request.allocated_amount
        lti = line.lti_grant_fmv_pool
        reserve = (
            _round_money(alloc.strategic_reserve * new_allocated / alloc.budget_for_allocation)
            if alloc.budget_for_allocation > 0
            else Decimal("0")
        )
        base, variable = _split_cash(max(new_allocated - lti - reserve, Decimal("0")))
        line.base_pool = base
        line.variable_pool = variable
        line.reserve_pool = reserve
        line.allocated_amount = new_allocated
    else:
        if request.base_pool is not None:
            line.base_pool = request.base_pool
        if request.variable_pool is not None:
            line.variable_pool = request.variable_pool
        if request.lti_grant_fmv_pool is not None:
            line.lti_grant_fmv_pool = request.lti_grant_fmv_pool
        if request.reserve_pool is not None:
            line.reserve_pool = request.reserve_pool
        line.allocated_amount = (
            line.base_pool + line.variable_pool + line.lti_grant_fmv_pool + line.reserve_pool
        )

    if request.notes is not None:
        line.notes = request.notes

    await db.flush()
    await db.refresh(line, ["updated_at"])
    await audit_log_service.log_action(
        db,
        actor_user_id=caller_user_id,
        action="BUDGET_LINE_UPDATED",
        tenant_id=tenant_id,
        resource_type="budget_allocation_line",
        resource_id=str(line.id),
        metadata={
            "allocation_id": str(alloc.id),
            "recipient_user_id": str(line.recipient_user_id),
            "allocated_amount": str(line.allocated_amount),
        },
    )
    return line


# ---- POST /budget-allocations/{id}/lines/{line_id}/refresh-view ----------
async def refresh_line_to_jvre(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    allocation_id: uuid.UUID,
    line_id: uuid.UUID,
    *,
    caller_user_id: uuid.UUID,
) -> BudgetAllocationLine:
    """Reset one line to its JVRE-recommended amount with proportional reserve."""
    alloc = await _get_owned_allocation_or_raise(db, tenant_id, allocation_id, caller_user_id)
    line = await budget_allocation_repository.get_line(db, alloc.id, line_id)
    if line is None:
        raise BudgetAllocationLineNotFoundError()

    cash, lti = await _compute_jvre_pool_for(db, tenant_id, alloc.cycle_id, line.recipient_user_id)
    jvre_total = cash + lti

    reserve = (
        _round_money(alloc.strategic_reserve * jvre_total / alloc.budget_for_allocation)
        if alloc.budget_for_allocation > 0
        else Decimal("0")
    )
    # See align_lines_with_jvre for why `reserve` isn't subtracted here.
    base, variable = _split_cash(cash)
    line.allocated_amount = base + variable + lti
    line.base_pool = base
    line.variable_pool = variable
    line.lti_grant_fmv_pool = lti
    line.reserve_pool = reserve

async def submit_budget_allocation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    allocation_id: uuid.UUID,
    *,
    caller_user_id: uuid.UUID,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> BudgetAllocation:
    """Lock the allocation and cascade child allocations to recipients.

    Validations:

    * Caller is the owner.
    * Status is PENDING.
    * Every direct report has a line on this allocation.
    * Sum of allocated_amounts <= budget_for_allocation.

    On success:

    * Parent: ``status = SUBMITTED``, ``submitted_at = now``,
      ``submitted_by_user_id = caller``.
    * Per line: a child ``BudgetAllocation`` is created (status PENDING)
      with ``total_pool = line.allocated_amount`` and
      ``parent_allocation_id`` pointing back. Idempotent: if a child
      already exists for the recipient (e.g. the user resubmits after
      a failed first submit), it's left in place.
    * Audit row: ``BUDGET_SUBMITTED``.
    """
    alloc = await _get_owned_allocation_or_raise(db, tenant_id, allocation_id, caller_user_id)

    # Every direct report must have a line.
    report_ids = set(
        await reporting_relationship_repository.report_ids(
            db, tenant_id, alloc.cycle_id, alloc.owner_user_id
        )
    )
    lines = await budget_allocation_repository.list_lines_for_allocation(db, alloc.id)
    line_recipient_ids = {line.recipient_user_id for line in lines}
    missing = sorted(report_ids - line_recipient_ids)
    if missing:
        raise MissingAllocationLinesError(missing)

    # Total within budget.
    total_allocated = sum((line.allocated_amount for line in lines), Decimal("0"))
    if total_allocated > alloc.budget_for_allocation:
        raise AllocationExceedsBudgetError(total_allocated, alloc.budget_for_allocation)

    # Lock parent.
    alloc.status = BudgetAllocationStatus.SUBMITTED.value
    alloc.submitted_at = datetime.now(UTC)
    alloc.submitted_by_user_id = caller_user_id

    # Cascade child allocations (idempotent).
    children_created = 0
    for line in lines:
        existing_child = await budget_allocation_repository.get_for_owner(
            db, tenant_id, alloc.cycle_id, line.recipient_user_id
        )
        if existing_child is not None:
            continue
        # The child's budget_for_allocation must cover their own subtree's
        # JVRE recommendation (== what they just received) with headroom,
        # never less — so we size it at 5% above what was handed down and
        # add the strategic reserve on top, rather than carving it out.
        mop_budget_for_allocation = (line.allocated_amount * Decimal("1.05")).quantize(Decimal("0.01"))
        mop_reserve = (mop_budget_for_allocation * Decimal("0.06")).quantize(Decimal("0.01"))
        child = BudgetAllocation(
            tenant_id=tenant_id,
            cycle_id=alloc.cycle_id,
            owner_user_id=line.recipient_user_id,
            parent_allocation_id=alloc.id,
            total_pool=mop_budget_for_allocation + mop_reserve,
            strategic_reserve=mop_reserve,
            budget_for_allocation=mop_budget_for_allocation,
            currency_code=line.currency_code,
            status=BudgetAllocationStatus.PENDING.value,
        )
        db.add(child)
        children_created += 1

    await db.flush()
    await db.refresh(alloc, ["updated_at"])

    # Atomic with the UoW — same transaction as the row updates above.
    await audit_log_service.log_action(
        db,
        actor_user_id=caller_user_id,
        action="BUDGET_SUBMITTED",
        tenant_id=tenant_id,
        resource_type="budget_allocation",
        resource_id=str(alloc.id),
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata={
            "cycle_id": str(alloc.cycle_id),
            "owner_user_id": str(alloc.owner_user_id),
            "total_pool": str(alloc.total_pool),
            "strategic_reserve": str(alloc.strategic_reserve),
            "budget_for_allocation": str(alloc.budget_for_allocation),
            "total_allocated": str(total_allocated),
            "lines_count": len(lines),
            "child_allocations_created": children_created,
        },
    )

    return alloc


# ---------------------------------------------------------------------------
# Write-side: MoP Pay Recommendation (Phase 5)
# ---------------------------------------------------------------------------
# Component codes — duplicated from PayComponent enum so the seed loop
# below can iterate without an enum dance. Mirrors the spec's column
# order on the screen (Base / Variable / TCC computed / LTI / Other).
_PAY_COMPONENT_BASE = "BASE_PAY"
_PAY_COMPONENT_VARIABLE = "VARIABLE_PAY"
_PAY_COMPONENT_LTI_FMV = "LTI_GRANT_FMV"
_PAY_COMPONENT_OTHER = "OTHER_REWARDS"
_PAY_COMPONENT_LTI_UNITS = "LTI_UNITS"

_ALL_PAY_COMPONENTS = (
    _PAY_COMPONENT_BASE,
    _PAY_COMPONENT_VARIABLE,
    _PAY_COMPONENT_LTI_FMV,
    _PAY_COMPONENT_OTHER,
    _PAY_COMPONENT_LTI_UNITS,
)


# ---- Helpers -------------------------------------------------------------
async def _resolve_relationship_kind(db: AsyncSession, subject_user_id: uuid.UUID) -> str:
    """Decide MGR_FOR_IC vs MOM_FOR_MGR from the subject's roles.

    If the subject holds ``MANAGER`` or ``MANAGER_OF_MANAGERS``, the
    actor must be one tier above (MoM recommending pay for MoP) →
    ``MOM_FOR_MGR``. Otherwise the subject is an IC and the actor is
    a ``MANAGER`` → ``MGR_FOR_IC``.

    For v0.1 the C-Suite-recommends-MoM tier doesn't exist; that
    materializes in v0.2.
    """
    subject_roles = await _user_role_codes(db, subject_user_id)
    if RoleCode.MANAGER in subject_roles or RoleCode.MANAGER_OF_MANAGERS in subject_roles:
        return "MOM_FOR_MGR"
    return "MGR_FOR_IC"


def _seed_components_from_jvre(
    db: AsyncSession,
    rec: PayRecommendation,
    snapshot: JvreSnapshot | None,
) -> None:
    """Create the five component rows for a fresh recommendation.

    Each row gets:
      * ``jvre_rec_value`` from the snapshot (or NULL if no snapshot).
      * ``mgr_rec_value`` initialized to the JVRE value (so ``Align
        with JVRE`` is the default starting point).
      * ``current_value`` left NULL — there is no actuals source in
        v0.1; the ingestion pipeline lands in v0.2. The screen renders
        ``—`` for NULL until then.
    """
    jvre_by_component: dict[str, Decimal | None] = {}
    current_by_component: dict[str, Decimal | None] = {}
    if snapshot is not None:
        jvre_by_component = {
            _PAY_COMPONENT_BASE: snapshot.recommended_base,
            _PAY_COMPONENT_VARIABLE: snapshot.recommended_variable,
            _PAY_COMPONENT_LTI_FMV: snapshot.recommended_lti_fmv,
            _PAY_COMPONENT_OTHER: snapshot.recommended_other_rewards,
            _PAY_COMPONENT_LTI_UNITS: (
                Decimal(snapshot.recommended_lti_units)
                if snapshot.recommended_lti_units is not None
                else None
            ),
        }
        current_by_component = {
            _PAY_COMPONENT_BASE: snapshot.current_base,
            _PAY_COMPONENT_VARIABLE: snapshot.current_variable,
            _PAY_COMPONENT_LTI_FMV: Decimal("0"),
            _PAY_COMPONENT_OTHER: Decimal("0"),
            _PAY_COMPONENT_LTI_UNITS: (
                Decimal(snapshot.current_fy_vesting_units)
                if snapshot.current_fy_vesting_units is not None
                else Decimal("0")
            ),
        }
    for component in _ALL_PAY_COMPONENTS:
        jvre_value = jvre_by_component.get(component)
        db.add(
            PayRecommendationComponent(
                recommendation_id=rec.id,
                component=component,
                current_value=current_by_component.get(component),
                jvre_rec_value=jvre_value,
                mgr_rec_value=jvre_value,
                mom_rec_value=None,
                currency_code=rec.currency_code,
            )
        )


async def _upsert_override(
    db: AsyncSession,
    rec: PayRecommendation,
    actor_user_id: uuid.UUID,
    request: PayRecommendationComponentUpdateRequest,
) -> None:
    """Upsert the ``pay_recommendation_overrides`` row for this actor.

    No-op if the request didn't carry any override metadata fields.
    Subsequent edits update the same row in place — there is one
    override row per (recommendation, actor) by unique constraint.
    """
    if (
        request.reason_code is None
        and request.role_criticality is None
        and request.promotion_consideration is None
    ):
        return

    existing = await pay_recommendation_repository.get_override(db, rec.id, actor_user_id)
    if existing is None:
        db.add(
            PayRecommendationOverride(
                recommendation_id=rec.id,
                actor_user_id=actor_user_id,
                reason_code=request.reason_code,
                role_criticality=request.role_criticality,
                promotion_consideration=bool(request.promotion_consideration),
            )
        )
        return

    if request.reason_code is not None:
        existing.reason_code = request.reason_code
    if request.role_criticality is not None:
        existing.role_criticality = request.role_criticality
    if request.promotion_consideration is not None:
        existing.promotion_consideration = request.promotion_consideration


# ---- POST /comp-cycles/{cycle_id}/recommendations ------------------------
async def get_or_create_recommendation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    subject_user_id: uuid.UUID,
) -> PayRecommendation:
    """Return the actor's recommendation for this subject; create if absent.

    Idempotent. On creation, components are seeded from the JVRE
    snapshot for the subject so the screen has values to render
    immediately.

    Authorization: the subject must be a direct report of the actor in
    this cycle. (Reporting-chain check inline; Phase 7 promotes to a
    dependency.)
    """
    # Reporting-chain check: subject must be actor's direct report.
    direct = await reporting_relationship_repository.get_manager_of(
        db, tenant_id, cycle_id, subject_user_id
    )
    if direct is None or direct.manager_user_id != actor_user_id:
        raise SubjectNotInReportingChainError()

    relationship_kind = await _resolve_relationship_kind(db, subject_user_id)

    existing = await pay_recommendation_repository.get_for_actor_subject(
        db, tenant_id, cycle_id, actor_user_id, subject_user_id, relationship_kind
    )
    if existing is not None:
        return existing

    # Resolve the cycle's currency for the new row.
    cycle = await compensation_cycle_repository.get_for_tenant(db, tenant_id, cycle_id)
    if cycle is None:
        raise CycleNotFoundError()

    rec = PayRecommendation(
        tenant_id=tenant_id,
        cycle_id=cycle_id,
        actor_user_id=actor_user_id,
        subject_user_id=subject_user_id,
        relationship_kind=relationship_kind,
        status="DRAFT",
        currency_code=cycle.currency_code,
    )
    db.add(rec)
    await db.flush()

    snapshot = await jvre_snapshot_repository.get_for_subject(
        db, tenant_id, cycle_id, subject_user_id
    )
    _seed_components_from_jvre(db, rec, snapshot)
    await db.flush()
    await audit_log_service.log_action(
        db,
        actor_user_id=actor_user_id,
        action="RECOMMENDATION_CREATED",
        tenant_id=tenant_id,
        resource_type="pay_recommendation",
        resource_id=str(rec.id),
        metadata={
            "cycle_id": str(cycle_id),
            "subject_user_id": str(subject_user_id),
            "relationship_kind": rec.relationship_kind,
        },
    )
    return rec


async def _get_owned_recommendation_or_raise(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    recommendation_id: uuid.UUID,
    caller_user_id: uuid.UUID,
    *,
    require_actor: bool = True,
    editable_statuses: tuple[str, ...] = ("DRAFT",),
) -> PayRecommendation:
    """Fetch + validate caller is the actor + status is editable.

    For Phase 5 we restrict writes to the original actor in DRAFT. The
    MoM-review write path in Phase 6 relaxes both: actor can be the
    upstream reviewer, status can be SUBMITTED/UNDER_REVIEW.
    """
    rec = await pay_recommendation_repository.get_for_tenant(db, tenant_id, recommendation_id)
    if rec is None:
        raise RecommendationNotFoundError()
    if require_actor and rec.actor_user_id != caller_user_id:
        raise SubjectNotInReportingChainError()
    if rec.status not in editable_statuses:
        raise RecommendationNotEditableError(rec.status)
    return rec


# Caller classification used by Phase 6 write endpoints. The same PUT
# component endpoint serves both the original actor (writes
# mgr_rec_value) and the upstream reviewer (writes mom_rec_value); the
# helper below picks the right column.
_CALLER_AS_ACTOR = "actor"
_CALLER_AS_REVIEWER = "reviewer"

# Statuses that accept actor edits (Phase 5).
_ACTOR_EDITABLE_STATUSES: tuple[str, ...] = ("DRAFT",)
# Statuses that accept reviewer edits (Phase 6). A REVISED row stays
# editable so the reviewer can iterate before approving.
_REVIEWER_EDITABLE_STATUSES: tuple[str, ...] = (
    "SUBMITTED",
    "UNDER_REVIEW",
    "REVISED",
)


async def _classify_caller_or_raise(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    rec: PayRecommendation,
    caller_user_id: uuid.UUID,
) -> str:
    """Return ``_CALLER_AS_ACTOR`` or ``_CALLER_AS_REVIEWER`` (string sentinels).

    Callers branch on the returned string rather than a boolean so both
    values are explicit in code review and grep.

    * Caller is the recommendation's ``actor_user_id`` → actor.
    * Caller is the actor's direct manager in this cycle → reviewer.
    * Otherwise → 403 (subject not in reporting chain).

    The status check is the caller's responsibility — actor edits
    happen on DRAFT; reviewer edits happen on SUBMITTED / UNDER_REVIEW
    / REVISED.
    """
    if rec.actor_user_id == caller_user_id:
        return _CALLER_AS_ACTOR
    actor_mgr = await reporting_relationship_repository.get_manager_of(
        db, tenant_id, rec.cycle_id, rec.actor_user_id
    )
    if actor_mgr is not None and actor_mgr.manager_user_id == caller_user_id:
        return _CALLER_AS_REVIEWER
    raise SubjectNotInReportingChainError()


# ---- PUT /pay-recommendations/{id}/components/{component} ----------------
async def update_recommendation_component(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    recommendation_id: uuid.UUID,
    component: str,
    request: PayRecommendationComponentUpdateRequest,
    *,
    caller_user_id: uuid.UUID,
) -> PayRecommendation:
    """Set one cell on the recommendation card.

    Dispatches on the caller's relationship to the recommendation:

    * **Caller is the actor (Phase 5 path)** — value lands in
      ``mgr_rec_value``. Status must be ``DRAFT``. Override metadata
      goes on the actor's own ``pay_recommendation_overrides`` row.
    * **Caller is the actor's manager (Phase 6 path)** — value lands
      in ``mom_rec_value``, leaving ``mgr_rec_value`` intact for full
      provenance. Status must be ``SUBMITTED`` / ``UNDER_REVIEW`` /
      ``REVISED``. First reviewer write also flips
      ``SUBMITTED → UNDER_REVIEW`` so the screen's tab badge updates.
      Override metadata goes on the reviewer's own override row,
      independent of the actor's.
    """
    if component not in _ALL_PAY_COMPONENTS:
        raise InvalidPayComponentError(component, list(_ALL_PAY_COMPONENTS))

    rec = await pay_recommendation_repository.get_for_tenant(db, tenant_id, recommendation_id)
    if rec is None:
        raise RecommendationNotFoundError()

    caller_kind = await _classify_caller_or_raise(db, tenant_id, rec, caller_user_id)

    editable_statuses = (
        _ACTOR_EDITABLE_STATUSES if caller_kind == _CALLER_AS_ACTOR else _REVIEWER_EDITABLE_STATUSES
    )
    if rec.status not in editable_statuses:
        raise RecommendationNotEditableError(rec.status)

    comp = await pay_recommendation_repository.get_component(db, rec.id, component)
    if comp is None:
        # Defensive: shouldn't happen since _seed_components_from_jvre
        # creates all five rows on rec creation. Materialize an empty
        # row and continue rather than 500ing.
        comp = PayRecommendationComponent(
            recommendation_id=rec.id,
            component=component,
            currency_code=rec.currency_code,
        )
        db.add(comp)
        await db.flush()

    if caller_kind == _CALLER_AS_ACTOR:
        comp.mgr_rec_value = request.value
    else:
        # Reviewer write: mom_rec_value gets the new value. The
        # mgr_rec_value column is untouched so the MoM card can render
        # all three rows (My Rec / Mgr Rec / JVRE Rec) for full
        # provenance. First reviewer write flips status.
        comp.mom_rec_value = request.value
        if rec.status == "SUBMITTED":
            rec.status = "UNDER_REVIEW"

    await _upsert_override(db, rec, caller_user_id, request)
    await db.flush()
    await audit_log_service.log_action(
        db,
        actor_user_id=caller_user_id,
        action="RECOMMENDATION_COMPONENT_UPDATED",
        tenant_id=tenant_id,
        resource_type="pay_recommendation",
        resource_id=str(rec.id),
        metadata={
            "cycle_id": str(rec.cycle_id),
            "component": component,
            "caller_kind": caller_kind,
            "value": str(request.value) if request.value is not None else None,
        },
    )
    return rec


# ---- POST /pay-recommendations/{id}/align-with-jvre ----------------------
async def align_recommendation_with_jvre(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    recommendation_id: uuid.UUID,
    *,
    caller_user_id: uuid.UUID,
) -> PayRecommendation:
    """Reset every component's ``mgr_rec_value`` to its JVRE rec.

    Doesn't touch override metadata or annotations — just the values.
    The card flips to JVRE Aligned because deviation is now zero.
    """
    rec = await _get_owned_recommendation_or_raise(db, tenant_id, recommendation_id, caller_user_id)
    components = await pay_recommendation_repository.list_components(db, rec.id)
    for comp in components:
        comp.mgr_rec_value = comp.jvre_rec_value
    await db.flush()
    await audit_log_service.log_action(
        db,
        actor_user_id=caller_user_id,
        action="RECOMMENDATION_ALIGNED_WITH_JVRE",
        tenant_id=tenant_id,
        resource_type="pay_recommendation",
        resource_id=str(rec.id),
        metadata={
            "cycle_id": str(rec.cycle_id),
            "subject_user_id": str(rec.subject_user_id),
        },
    )
    return rec


# ---- POST /pay-recommendations/{id}/save ---------------------------------
async def save_recommendation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    recommendation_id: uuid.UUID,
    *,
    caller_user_id: uuid.UUID,
) -> PayRecommendation:
    """Stamp ``saved_at`` so the "1 of N Completed" counter advances."""
    rec = await _get_owned_recommendation_or_raise(db, tenant_id, recommendation_id, caller_user_id)
    rec.saved_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(rec, ["updated_at"])
    return rec


# ---- POST /comp-cycles/{cycle_id}/my-recommendations/submit --------------
async def submit_my_recommendations(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    *,
    caller_user_id: uuid.UUID,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> list[PayRecommendation]:
    """Submit every DRAFT recommendation the caller authored in this cycle.

    Validations:

    * Cycle exists and is ACTIVE.
    * The caller has at least one DRAFT recommendation to submit.
    * Every direct report of the caller in this cycle has a
      recommendation row (i.e. the actor walked through every card).

    On success:

    * Each rec flips ``DRAFT → SUBMITTED`` with ``submitted_at = now``.
    * One ``RECOMMENDATION_SUBMITTED`` audit row per rec is written
      (atomic with the UoW so partial submits never leak audit rows).
    """
    cycle = await get_cycle(db, tenant_id, cycle_id)
    if cycle.status != "ACTIVE":
        raise BudgetAllocationNotEditableError(cycle.status)

    # Every direct report should have a recommendation.
    report_ids = set(
        await reporting_relationship_repository.report_ids(db, tenant_id, cycle_id, caller_user_id)
    )
    recs = await pay_recommendation_repository.list_for_actor(
        db, tenant_id, cycle_id, caller_user_id
    )
    rec_subject_ids = {r.subject_user_id for r in recs}
    missing = sorted(report_ids - rec_subject_ids)
    if missing:
        # Reuse the budget-side error code; same shape, same UX.
        raise MissingAllocationLinesError(missing)

    # Filter to drafts; submit them all.
    drafts = [r for r in recs if r.status == "DRAFT"]
    if not drafts:
        # Nothing to do — caller already submitted earlier. Idempotent.
        return recs

    now = datetime.now(UTC)
    for rec in drafts:
        rec.status = "SUBMITTED"
        rec.submitted_at = now
        if rec.saved_at is None:
            # Submit implies save; backfill so the counter on the
            # post-submit screen matches.
            rec.saved_at = now

    await db.flush()

    # One audit row per submitted recommendation. Same UoW.
    for rec in drafts:
        await audit_log_service.log_action(
            db,
            actor_user_id=caller_user_id,
            action="RECOMMENDATION_SUBMITTED",
            tenant_id=tenant_id,
            resource_type="pay_recommendation",
            resource_id=str(rec.id),
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={
                "cycle_id": str(rec.cycle_id),
                "actor_user_id": str(rec.actor_user_id),
                "subject_user_id": str(rec.subject_user_id),
                "relationship_kind": rec.relationship_kind,
            },
        )

    return drafts


# ---------------------------------------------------------------------------
# Write-side: MoM Pay Review (Phase 6)
# ---------------------------------------------------------------------------


async def _require_caller_is_reviewer(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    rec: PayRecommendation,
    caller_user_id: uuid.UUID,
) -> None:
    """Approve / revise / annotate require the caller to be the
    upstream reviewer (the actor's direct manager in this cycle). The
    actor themselves cannot approve their own recommendation."""
    if rec.actor_user_id == caller_user_id:
        raise SubjectNotInReportingChainError()
    actor_mgr = await reporting_relationship_repository.get_manager_of(
        db, tenant_id, rec.cycle_id, rec.actor_user_id
    )
    if actor_mgr is None or actor_mgr.manager_user_id != caller_user_id:
        raise SubjectNotInReportingChainError()


# ---- POST /pay-recommendations/{id}/approve ------------------------------
async def approve_recommendation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    recommendation_id: uuid.UUID,
    *,
    caller_user_id: uuid.UUID,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> PayRecommendation:
    """Approve the recommendation. Caller must be the upstream reviewer."""
    rec = await pay_recommendation_repository.get_for_tenant(db, tenant_id, recommendation_id)
    if rec is None:
        raise RecommendationNotFoundError()
    await _require_caller_is_reviewer(db, tenant_id, rec, caller_user_id)

    if rec.status not in _REVIEWER_EDITABLE_STATUSES:
        raise RecommendationNotEditableError(rec.status)

    now = datetime.now(UTC)
    rec.status = "APPROVED"
    rec.approved_at = now
    await db.flush()

    await audit_log_service.log_action(
        db,
        actor_user_id=caller_user_id,
        action="RECOMMENDATION_APPROVED",
        tenant_id=tenant_id,
        resource_type="pay_recommendation",
        resource_id=str(rec.id),
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata={
            "cycle_id": str(rec.cycle_id),
            "subject_user_id": str(rec.subject_user_id),
            "actor_user_id": str(rec.actor_user_id),
            "relationship_kind": rec.relationship_kind,
        },
    )
    return rec


# ---- POST /pay-recommendations/{id}/revise -------------------------------
async def revise_recommendation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    recommendation_id: uuid.UUID,
    request: RecommendationReviseRequest,
    *,
    caller_user_id: uuid.UUID,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> PayRecommendation:
    """Flip status to REVISED + append the reviewer's annotation.

    Used when the MoM has overridden values and wants to flag that the
    submission was modified rather than approved-as-is. The annotation
    feeds the "Christy's action: …" strip on the screen. The recommen-
    dation stays editable in REVISED status so the reviewer can
    iterate further before approving.
    """
    rec = await pay_recommendation_repository.get_for_tenant(db, tenant_id, recommendation_id)
    if rec is None:
        raise RecommendationNotFoundError()
    await _require_caller_is_reviewer(db, tenant_id, rec, caller_user_id)

    if rec.status not in _REVIEWER_EDITABLE_STATUSES:
        raise RecommendationNotEditableError(rec.status)

    rec.status = "REVISED"

    if request.annotation_text:
        db.add(
            PayRecommendationAnnotation(
                recommendation_id=rec.id,
                actor_user_id=caller_user_id,
                text=request.annotation_text,
            )
        )

    await db.flush()

    await audit_log_service.log_action(
        db,
        actor_user_id=caller_user_id,
        action="RECOMMENDATION_REVISED",
        tenant_id=tenant_id,
        resource_type="pay_recommendation",
        resource_id=str(rec.id),
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata={
            "cycle_id": str(rec.cycle_id),
            "subject_user_id": str(rec.subject_user_id),
            "actor_user_id": str(rec.actor_user_id),
            "relationship_kind": rec.relationship_kind,
            "annotation_attached": request.annotation_text is not None,
        },
    )
    return rec


# ---- POST /pay-recommendations/{id}/annotations --------------------------
async def add_recommendation_annotation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    recommendation_id: uuid.UUID,
    request: AnnotationCreateRequest,
    *,
    caller_user_id: uuid.UUID,
) -> PayRecommendationAnnotation:
    """Append a free-text note. Caller must be in the recommendation's
    read-chain (actor or upstream reviewer)."""
    rec = await pay_recommendation_repository.get_for_tenant(db, tenant_id, recommendation_id)
    if rec is None:
        raise RecommendationNotFoundError()
    # Same read-chain check used by GET /pay-recommendations/{id}.
    if not await _caller_can_read_recommendation(db, tenant_id, rec, caller_user_id):
        raise SubjectNotInReportingChainError()

    annotation = PayRecommendationAnnotation(
        recommendation_id=rec.id,
        actor_user_id=caller_user_id,
        text=request.text,
    )
    db.add(annotation)
    await db.flush()
    return annotation


# ---------------------------------------------------------------------------
# iQuest AI — engine-output sync + rationale persistence
# ---------------------------------------------------------------------------
async def sync_engine_output(
    db: AsyncSession,
    eng: IquestEngineOutput,
) -> IquestEngineOutput:
    """Re-sync mutable pay fields from live tables into the engine-output row
    so the AI prompt always reflects current data.

    Pulls from jvre_snapshot, market_benchmark, and pay_recommendation_components
    on every rationale request; does not commit (caller owns the transaction).
    """
    bonus_pct = eng.target_bonus_pct or Decimal("0.12")

    # ── 1. jvre_snapshot: current pay + JVRE score + promotion readiness ──
    snap = await jvre_snapshot_repository.get_for_subject(
        db, eng.tenant_id, eng.cycle_id, eng.subject_user_id
    )
    if snap is not None:
        if snap.current_base is not None:
            eng.current_base_inr = snap.current_base
            eng.total_cash_inr = (snap.current_base * (Decimal("1") + bonus_pct)).quantize(
                Decimal("1")
            )
        if snap.jvre_score is not None:
            eng.jvre_score = snap.jvre_score
            score = float(snap.jvre_score)
            if score >= 7:
                eng.jvre_tier = "HIGH"
            elif score >= 4:
                eng.jvre_tier = "MODERATE"
            else:
                eng.jvre_tier = "LOW"
        eng.promotion_flag = snap.promotion_readiness == JvrePromotionReadiness.READY.value

    # ── 2. market_benchmark: p50 + compa-ratio ────────────────────────────
    bench = await market_benchmark_repository.get_for_subject(
        db, eng.tenant_id, eng.subject_user_id
    )
    if bench is not None:
        p50 = bench.target_pay
        eng.external_cr = bench.compa_ratio
        eng.effective_p50 = p50
        eng.benchmark_p50 = p50
        eng.benchmark_p25 = (p50 * Decimal("0.85")).quantize(Decimal("1"))
        eng.benchmark_p75 = (p50 * Decimal("1.15")).quantize(Decimal("1"))
    else:
        p50 = eng.benchmark_p50 or eng.current_base_inr or Decimal("1")

    # ── 3. pay_recommendation_components: latest BASE_PAY final value ─────
    # Priority across layers: mom_rec_value > mgr_rec_value > jvre_rec_value
    base_comp_row = (
        await db.execute(
            select(PayRecommendationComponent)
            .join(
                PayRecommendation,
                PayRecommendationComponent.recommendation_id == PayRecommendation.id,
            )
            .where(
                PayRecommendation.tenant_id == eng.tenant_id,
                PayRecommendation.cycle_id == eng.cycle_id,
                PayRecommendation.subject_user_id == eng.subject_user_id,
                PayRecommendationComponent.component == "BASE_PAY",
            )
            .order_by(PayRecommendation.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    rec_base: Decimal | None = None
    if base_comp_row is not None:
        rec_base = (
            base_comp_row.mom_rec_value
            or base_comp_row.mgr_rec_value
            or base_comp_row.jvre_rec_value
        )
    if rec_base is None and snap is not None:
        rec_base = snap.recommended_base
    if rec_base is None:
        rec_base = eng.rec_new_base_inr or eng.current_base_inr

    # ── 4. Recompute all derived recommendation fields ────────────────────
    current_base = eng.current_base_inr or Decimal("1")
    eng.rec_new_base_inr = rec_base
    eng.rec_increase_pct = ((rec_base - current_base) / current_base).quantize(Decimal("0.0001"))
    eng.rec_total_cash_inr = (rec_base * (Decimal("1") + bonus_pct)).quantize(Decimal("1"))
    eng.capped_new_base_inr = rec_base
    eng.capped_rec_increase_pct = eng.rec_increase_pct
    eng.capped_total_cash_inr = eng.rec_total_cash_inr
    eng.new_cr_after_rec = (rec_base / p50).quantize(Decimal("0.0001"))
    if eng.policy_target_cr is not None:
        eng.rem_gap_to_policy_pctile = (
            (eng.policy_target_cr - eng.new_cr_after_rec) * Decimal("100")
        ).quantize(Decimal("0.01"))

    return eng


async def persist_generated_rationale(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    subject_user_id: uuid.UUID,
    rationale_text: str,
    model_id: str,
) -> None:
    """Upsert the generated rationale into jvre_rationale and iquest_engine_output.

    Does not commit — per the Unit-of-Work contract in
    ``app.dependencies.db_dependency``, ``get_db`` commits once when the
    request finishes. This function only flushes so server-generated
    columns are materialized for any subsequent read in the same request.
    """
    await jvre_rationale_repository.upsert(
        db, tenant_id, cycle_id, subject_user_id, rationale_text, model_id
    )
    await db.execute(
        update(IquestEngineOutput)
        .where(
            IquestEngineOutput.cycle_id == cycle_id,
            IquestEngineOutput.subject_user_id == subject_user_id,
        )
        .values(rationale=rationale_text)
    )
    await db.flush()


# Imported for symmetry; service modules in this codebase generally
# avoid silent unused warnings from optional imports.
_ = (BudgetAllocation, select)
