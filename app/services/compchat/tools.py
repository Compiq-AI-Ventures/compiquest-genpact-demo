"""Layer 5/6 — the deterministic tool registry.

The complete tool surface available to the pipeline. **No tool is
called by the LLM** — the pipeline selects them via the static
:data:`INTENT_TOOLS` map and invokes them in Python. Each tool runs a
parameterised query against the tenant-scoped session and returns a
typed result object (never a raw ORM row).

Identity bridge
---------------
RBAC and the resolver work in ``users.id`` (UUID) space. The Tessot
master-data tables key on a string ``employee_id`` (e.g. ``"EMP00042"``).
:func:`resolve_tessot_id` bridges the two via ``IquestEngineOutput``,
which carries both. Tools accept the UUID and bridge internally so
callers never juggle the string id.
"""

from __future__ import annotations

import statistics
import uuid
from datetime import date

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget_allocation import BudgetAllocation, BudgetAllocationLine
from app.models.genpact_master_data import GenpactEmployeeMaster
from app.models.iquest_engine_output import IquestEngineOutput
from app.models.pay_recommendation import (
    PayComponent,
    PayRecommendation,
    PayRecommendationComponent,
    PayRecommendationRelationshipKind,
)
from app.repositories import (
    pay_recommendation_repository,
    reporting_relationship_repository,
)

from .schemas import (
    AnalyticsScope,
    BudgetHeadroom,
    Comparison,
    Compensation,
    EmployeeContext,
    IntentType,
    PayRecBase,
    Performance,
    PromotionEvent,
    PromotionHistory,
    Recommendation,
    Team,
    TeamAnalytics,
    TeamMember,
)


def _int(val: object) -> int | None:
    return int(val) if val is not None else None


def _flt(val: object) -> float | None:
    return float(val) if val is not None else None

# ---------------------------------------------------------------------------
# Intent → permitted tool set (framework Layer 4/5).
# Selection is structural: a TEAM_QUERY's list never contains
# "get_compensation", so it cannot reach compensation data regardless of
# what the question's words say.
# ---------------------------------------------------------------------------
INTENT_TOOLS: dict[IntentType, tuple[str, ...]] = {
    IntentType.COMPENSATION_QUERY: ("get_employee_context", "get_compensation"),
    IntentType.PERFORMANCE_QUERY: ("get_employee_context", "get_performance"),
    IntentType.PROMOTION_QUERY: ("get_employee_context", "get_promotion_history"),
    IntentType.TEAM_QUERY: ("get_employee_context", "get_team"),
    IntentType.COMPARISON_QUERY: ("get_employee_context", "compare_compensation"),
    IntentType.ANALYTICS_QUERY: ("get_employee_context", "get_analytics"),
    # Population-level batch report; handled outside the per-subject path.
    IntentType.REPORT_REQUEST: (),
    IntentType.UNKNOWN: (),
}

# Fallback fiscal year when the question names none and the DB is empty.
_FALLBACK_FY = 2026


# ---------------------------------------------------------------------------
# FX helper — master data (genpact_employee_master, genpact_benchmark) is in
# each employee's LOCAL currency, but iQuest AI's answers must be in USD
# (the tenant's reporting currency). This cache loads USD-relative rates
# once per (tenant, fiscal_year) and converts local → USD via a plain divide.
# ---------------------------------------------------------------------------
_FX_CACHE: dict[tuple[uuid.UUID, str], dict[str, float]] = {}


async def _fx_local_per_usd(
    db: AsyncSession, tenant_id: uuid.UUID, fiscal_year: int
) -> dict[str, float]:
    key = (tenant_id, str(fiscal_year))
    if key in _FX_CACHE:
        return _FX_CACHE[key]
    rows = (
        await db.execute(
            text(
                "SELECT local_currency, conversion_value FROM genpact_currency_master "
                "WHERE tenant_id=:tid AND reporting_cycle=:yr AND reporting_currency='USD'"
            ),
            {"tid": str(tenant_id), "yr": str(fiscal_year)},
        )
    ).mappings().all()
    table = {r["local_currency"]: float(r["conversion_value"]) for r in rows}
    table.setdefault("USD", 1.0)
    _FX_CACHE[key] = table
    return table


def _to_usd(amount: object, ccy: str | None, table: dict[str, float]) -> int:
    """Convert a local-currency amount to a USD integer.

    Uses the FY rate table loaded via :func:`_fx_local_per_usd`. Unknown
    currency codes fall through to a 1.0 rate (assume already USD).
    """
    if amount in (None, ""):
        return 0
    try:
        val = float(amount)
    except (TypeError, ValueError):
        return 0
    rate = table.get(ccy or "USD", 1.0) or 1.0
    return int(round(val / rate))


# ---------------------------------------------------------------------------
# Identity + fiscal-year helpers
# ---------------------------------------------------------------------------
async def resolve_tessot_id(
    db: AsyncSession, tenant_id: uuid.UUID, subject_user_id: uuid.UUID
) -> str | None:
    """Map a ``users.id`` UUID to its Tessot ``employee_id`` string."""
    row = await db.execute(
        select(IquestEngineOutput.employee_id)
        .where(
            IquestEngineOutput.tenant_id == tenant_id,
            IquestEngineOutput.subject_user_id == subject_user_id,
        )
        .limit(1)
    )
    return row.scalar_one_or_none()


async def default_fiscal_year(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    """The most recent fiscal year present in the Tessot base data."""
    row = await db.execute(
        select(GenpactEmployeeMaster.fiscal_year)
        .where(GenpactEmployeeMaster.tenant_id == tenant_id)
        .order_by(GenpactEmployeeMaster.fiscal_year.desc())
        .limit(1)
    )
    fy = row.scalar_one_or_none()
    return int(fy) if fy else _FALLBACK_FY


async def _base_row(
    db: AsyncSession, tenant_id: uuid.UUID, employee_id: str, fiscal_year: int
) -> GenpactEmployeeMaster | None:
    row = await db.execute(
        select(GenpactEmployeeMaster).where(
            GenpactEmployeeMaster.tenant_id == tenant_id,
            GenpactEmployeeMaster.employee_id == employee_id,
            GenpactEmployeeMaster.fiscal_year == str(fiscal_year),
        )
    )
    return row.scalar_one_or_none()


async def _manager_name(
    db: AsyncSession, tenant_id: uuid.UUID, manager_employee_id: str, fiscal_year: int
) -> str | None:
    if not manager_employee_id:
        return None
    row = await db.execute(
        select(GenpactEmployeeMaster.employee_name).where(
            GenpactEmployeeMaster.tenant_id == tenant_id,
            GenpactEmployeeMaster.employee_id == manager_employee_id,
            GenpactEmployeeMaster.fiscal_year == str(fiscal_year),
        )
    )
    return row.scalar_one_or_none()


def _record_id(table: str, employee_id: str, fiscal_year: int) -> str:
    return f"{table}_{employee_id}_{fiscal_year}"


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------
async def get_employee_context(
    db: AsyncSession, tenant_id: uuid.UUID, employee_id: str, fiscal_year: int
) -> EmployeeContext | None:
    row = await _base_row(db, tenant_id, employee_id, fiscal_year)
    if row is None:
        return None
    return EmployeeContext(
        name=row.employee_name,
        role=row.designation,
        level=row.job_level,
        manager=await _manager_name(db, tenant_id, row.manager_employee_id, fiscal_year),
        department=row.department,
        job_family=row.job_family,
        hire_date=row.joining_date.isoformat() if row.joining_date else None,
    )


async def get_compensation(
    db: AsyncSession, tenant_id: uuid.UUID, employee_id: str, fiscal_year: int
) -> Compensation | None:
    row = await _base_row(db, tenant_id, employee_id, fiscal_year)
    if row is None:
        return None
    fx = await _fx_local_per_usd(db, tenant_id, fiscal_year)
    local_ccy = row.currency or "USD"
    compa = float(row.external_compa_post or 0)
    # Local-currency figures for internal math; ratios (compa) are
    # dimensionless so they don't need converting.
    base_local = int(row.base_salary_post or 0)
    p50_local = round(base_local / compa) if compa else 0
    return Compensation(
        base_salary=_to_usd(base_local, local_ccy, fx),
        bonus_actual=_to_usd(row.variable_post, local_ccy, fx),
        bonus_target_pct=float(row.target_variable_pct or 0),
        total_cash=_to_usd(row.tcc_post, local_ccy, fx),
        lti_value=_to_usd(row.lti_grant_value, local_ccy, fx) if row.lti_eligible else 0,
        compa_ratio=compa,
        benchmark_p50=_to_usd(p50_local, local_ccy, fx),
        currency="USD",
        source="genpact_employee_master",
        record_id=_record_id("COMP", employee_id, fiscal_year),
    )


async def get_recommendation(
    db: AsyncSession, tenant_id: uuid.UUID, subject_user_id: uuid.UUID
) -> Recommendation | None:
    """Load the JVRE engine output for the subject — the data the
    rationale is about. Keyed by ``subject_user_id`` (UUID), not the
    Tessot string id, since this is the live recommendation record."""
    row = await db.execute(
        select(IquestEngineOutput)
        .where(
            IquestEngineOutput.tenant_id == tenant_id,
            IquestEngineOutput.subject_user_id == subject_user_id,
        )
        .order_by(IquestEngineOutput.cycle_id)
        .limit(1)
    )
    eng = row.scalar_one_or_none()
    if eng is None:
        return None
    return Recommendation(
        compa_ratio=_flt(eng.external_cr),
        new_compa_ratio_after_rec=_flt(eng.new_cr_after_rec),
        target_bonus_pct=_flt(eng.target_bonus_pct),
        total_cash=_int(eng.total_cash_inr),
        rec_total_cash=_int(eng.rec_total_cash_inr),
        benchmark_p25=_int(eng.benchmark_p25),
        benchmark_p50=_int(eng.benchmark_p50),
        benchmark_p75=_int(eng.benchmark_p75),
        months_since_last_increase=eng.months_since_last_increase,
        unvested_usd=_int(eng.unvested_usd),
        next_vest_date=eng.next_vest_date.isoformat() if eng.next_vest_date else None,
        months_to_next_vest=_flt(eng.months_to_next_vest),
        jvre_score=_flt(eng.jvre_score),
        jvre_tier=eng.jvre_tier,
        rating_band=eng.rating_band,
        promotion_flag=bool(eng.promotion_flag) if eng.promotion_flag is not None else None,
        source="iquest_engine_output",
        record_id=f"JVRE_{eng.employee_id or subject_user_id}_{eng.cycle_id}",
    )


async def get_pay_recommendation_base(
    db: AsyncSession, tenant_id: uuid.UUID, cycle_id: uuid.UUID, subject_user_id: uuid.UUID
) -> PayRecBase | None:
    """The four distinct BASE_PAY figures (current / JVRE / MoP / MoM)
    from the subject's MoP-owned recommendation. Returns ``None`` when no
    such recommendation/component exists (caller falls back to the
    engine number)."""
    mgr = await reporting_relationship_repository.get_manager_of(
        db, tenant_id, cycle_id, subject_user_id
    )
    if mgr is None:
        return None
    # The owning row is MGR_FOR_IC for an IC subject, MOM_FOR_MGR for a
    # manager subject — try both rather than re-deriving the subject's tier.
    rec = None
    for kind in (
        PayRecommendationRelationshipKind.MGR_FOR_IC.value,
        PayRecommendationRelationshipKind.MOM_FOR_MGR.value,
    ):
        rec = await pay_recommendation_repository.get_for_actor_subject(
            db, tenant_id, cycle_id, mgr.manager_user_id, subject_user_id, kind
        )
        if rec is not None:
            break
    if rec is None:
        return None
    comp = await pay_recommendation_repository.get_component(
        db, rec.id, PayComponent.BASE_PAY.value
    )
    if comp is None:
        return None
    return PayRecBase(
        current=_int(comp.current_value),
        jvre_recommended=_int(comp.jvre_rec_value),
        manager_recommended=_int(comp.mgr_rec_value),
        mom_recommended=_int(comp.mom_rec_value),
        currency=rec.currency_code or "INR",
        source="pay_recommendation_components",
        record_id=f"PAYREC_{rec.id}_BASE_PAY",
    )


async def get_engine_base(
    db: AsyncSession, tenant_id: uuid.UUID, subject_user_id: uuid.UUID
) -> PayRecBase | None:
    """Fallback base figures from the JVRE engine output when no
    pay-recommendation component row exists. Only current and the JVRE
    number are known here (no manager/MoM value)."""
    row = await db.execute(
        select(IquestEngineOutput)
        .where(
            IquestEngineOutput.tenant_id == tenant_id,
            IquestEngineOutput.subject_user_id == subject_user_id,
        )
        .order_by(IquestEngineOutput.cycle_id)
        .limit(1)
    )
    eng = row.scalar_one_or_none()
    if eng is None:
        return None
    return PayRecBase(
        current=_int(eng.current_base_inr),
        jvre_recommended=_int(eng.rec_new_base_inr),
        source="iquest_engine_output",
        record_id=f"JVRE_{eng.employee_id or subject_user_id}_{eng.cycle_id}",
    )


async def get_budget_headroom(
    db: AsyncSession, tenant_id: uuid.UUID, cycle_id: uuid.UUID, subject_user_id: uuid.UUID
) -> BudgetHeadroom | None:
    """Remaining, uncommitted budget pool for the subject's manager this
    cycle: allocated_amount minus the sum of BASE_PAY manager_rec_value
    across every one of the manager's direct reports (mirrors the
    manager's own Budget Planner card). Returns ``None`` when the
    subject has no manager in this cycle or the manager has no
    allocation (e.g. the allocation hasn't been created yet).
    """
    mgr = await reporting_relationship_repository.get_manager_of(
        db, tenant_id, cycle_id, subject_user_id
    )
    if mgr is None:
        return None
    manager_user_id = mgr.manager_user_id

    line_row = await db.execute(
        select(BudgetAllocationLine)
        .join(BudgetAllocation, BudgetAllocation.id == BudgetAllocationLine.allocation_id)
        .where(
            BudgetAllocation.tenant_id == tenant_id,
            BudgetAllocation.cycle_id == cycle_id,
            BudgetAllocationLine.recipient_user_id == manager_user_id,
        )
    )
    line = line_row.scalar_one_or_none()

    if line is not None:
        allocated_amount = line.allocated_amount
        currency = line.currency_code or "INR"
        record_id = f"BUDGETLINE_{line.id}"
    else:
        alloc_row = await db.execute(
            select(BudgetAllocation).where(
                BudgetAllocation.tenant_id == tenant_id,
                BudgetAllocation.cycle_id == cycle_id,
                BudgetAllocation.owner_user_id == manager_user_id,
            )
        )
        alloc = alloc_row.scalar_one_or_none()
        if alloc is None:
            return None
        allocated_amount = alloc.budget_for_allocation
        currency = alloc.currency_code or "INR"
        record_id = f"BUDGETALLOC_{alloc.id}"

    report_ids = await reporting_relationship_repository.report_ids(
        db, tenant_id, cycle_id, manager_user_id
    )
    total_recommended = 0
    if report_ids:
        spent_row = await db.execute(
            select(func.coalesce(func.sum(PayRecommendationComponent.mgr_rec_value), 0))
            .join(PayRecommendation, PayRecommendation.id == PayRecommendationComponent.recommendation_id)
            .where(
                PayRecommendation.tenant_id == tenant_id,
                PayRecommendation.cycle_id == cycle_id,
                PayRecommendation.actor_user_id == manager_user_id,
                PayRecommendation.subject_user_id.in_(report_ids),
                PayRecommendationComponent.component == PayComponent.BASE_PAY.value,
            )
        )
        total_recommended = spent_row.scalar_one()

    allocated_int = _int(allocated_amount)
    recommended_int = int(total_recommended)
    remaining = (
        allocated_int - recommended_int if allocated_int is not None else None
    )

    return BudgetHeadroom(
        allocated_amount=allocated_int,
        total_recommended=recommended_int,
        remaining_headroom=remaining,
        currency=currency,
        source="budget_allocations",
        record_id=record_id,
    )


async def get_performance(
    db: AsyncSession, tenant_id: uuid.UUID, employee_id: str, fiscal_year: int
) -> Performance | None:
    row = await _base_row(db, tenant_id, employee_id, fiscal_year)
    if row is None:
        return None
    return Performance(
        rating=float(row.performance_rating),
        promotion_flag=bool(row.promotion_flag),
        source="genpact_employee_master",
        record_id=_record_id("PERF", employee_id, fiscal_year),
    )


async def get_team(
    db: AsyncSession, tenant_id: uuid.UUID, employee_id: str, fiscal_year: int
) -> Team | None:
    manager_row = await _base_row(db, tenant_id, employee_id, fiscal_year)
    if manager_row is None:
        return None
    reports = await db.execute(
        select(
            GenpactEmployeeMaster.employee_id,
            GenpactEmployeeMaster.employee_name,
            GenpactEmployeeMaster.job_level,
        ).where(
            GenpactEmployeeMaster.tenant_id == tenant_id,
            GenpactEmployeeMaster.manager_employee_id == employee_id,
            GenpactEmployeeMaster.fiscal_year == str(fiscal_year),
        )
    )
    members = [
        TeamMember(employee_id=r.employee_id, name=r.employee_name, level=r.job_level)
        for r in reports.all()
    ]
    return Team(
        direct_reports=members,
        span_direct=manager_row.span_direct,
        span_indirect=manager_row.span_indirect,
        source="genpact_employee_master",
        record_id=_record_id("TEAM", employee_id, fiscal_year),
    )


async def get_promotion_history(
    db: AsyncSession, tenant_id: uuid.UUID, employee_id: str, fiscal_year: int
) -> PromotionHistory | None:
    """Reconstruct a promotion timeline by scanning the per-fiscal-year
    rows for level changes. ``fiscal_year`` bounds the upper edge."""
    rows = await db.execute(
        select(GenpactEmployeeMaster)
        .where(
            GenpactEmployeeMaster.tenant_id == tenant_id,
            GenpactEmployeeMaster.employee_id == employee_id,
            GenpactEmployeeMaster.fiscal_year <= str(fiscal_year),
        )
        .order_by(GenpactEmployeeMaster.fiscal_year.asc())
    )
    history = rows.scalars().all()
    if not history:
        return None

    events: list[PromotionEvent] = []
    for r in history:
        if r.previous_job_level and r.job_level and r.previous_job_level != r.job_level:
            events.append(
                PromotionEvent(
                    fiscal_year=int(r.fiscal_year),
                    from_level=r.previous_job_level,
                    to_level=r.job_level,
                    date=r.last_promotion_date.isoformat()
                    if r.last_promotion_date and r.last_promotion_date.year > 1970
                    else None,
                )
            )

    latest = history[-1]
    months_since: int | None = None
    if latest.last_promotion_date and latest.last_promotion_date.year > 1970:
        today = date.today()
        months_since = (today.year - latest.last_promotion_date.year) * 12 + (
            today.month - latest.last_promotion_date.month
        )

    return PromotionHistory(
        promotions=events,
        months_since_last=months_since,
        source="genpact_employee_master",
        record_id=_record_id("PROMO", employee_id, fiscal_year),
    )


async def compare_compensation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    employee_id_a: str,
    employee_id_b: str,
    fiscal_year: int,
    *,
    name_a: str,
    name_b: str,
) -> Comparison | None:
    comp_a = await get_compensation(db, tenant_id, employee_id_a, fiscal_year)
    comp_b = await get_compensation(db, tenant_id, employee_id_b, fiscal_year)
    if comp_a is None or comp_b is None:
        return None
    delta_salary = None
    if comp_a.base_salary is not None and comp_b.base_salary is not None:
        delta_salary = comp_a.base_salary - comp_b.base_salary
    delta_bonus = None
    if comp_a.bonus_actual is not None and comp_b.bonus_actual is not None:
        delta_bonus = comp_a.bonus_actual - comp_b.bonus_actual
    return Comparison(
        employee_a=comp_a,
        employee_b=comp_b,
        name_a=name_a,
        name_b=name_b,
        delta_salary=delta_salary,
        delta_bonus=delta_bonus,
    )


async def get_analytics(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    employee_id: str,
    fiscal_year: int,
    *,
    scope: AnalyticsScope,
) -> TeamAnalytics | None:
    """Aggregate statistics across the requester's team or the subject's
    job family. Only aggregates leave the tool — never individual rows."""
    anchor = await _base_row(db, tenant_id, employee_id, fiscal_year)
    if anchor is None:
        return None

    if scope is AnalyticsScope.TEAM:
        group_label = anchor.employee_name
        predicate = GenpactEmployeeMaster.manager_employee_id == employee_id
    else:
        group_label = anchor.job_family
        predicate = GenpactEmployeeMaster.job_family == anchor.job_family

    rows = await db.execute(
        select(
            GenpactEmployeeMaster.base_salary_post,
            GenpactEmployeeMaster.external_compa_post,
            GenpactEmployeeMaster.currency,
        ).where(
            GenpactEmployeeMaster.tenant_id == tenant_id,
            GenpactEmployeeMaster.fiscal_year == str(fiscal_year),
            predicate,
        )
    )
    records = rows.all()
    if not records:
        return None

    # Convert each employee's base to USD before aggregating — otherwise a
    # cross-currency team gives a mean/median that mixes INR + PLN + USD
    # numerals as if they were the same currency.
    fx = await _fx_local_per_usd(db, tenant_id, fiscal_year)
    salaries_usd = [
        _to_usd(r.base_salary_post, r.currency, fx)
        for r in records
        if r.base_salary_post
    ]
    ratios = [float(r.external_compa_post) for r in records if r.external_compa_post]

    return TeamAnalytics(
        scope=scope,
        group_label=group_label,
        headcount=len(records),
        avg_base_salary=round(statistics.mean(salaries_usd)) if salaries_usd else None,
        median_base_salary=round(statistics.median(salaries_usd)) if salaries_usd else None,
        avg_compa_ratio=round(statistics.mean(ratios), 2) if ratios else None,
        source="genpact_employee_master",
        record_id=f"ANALYTICS_{scope.value}_{group_label}_{fiscal_year}",
    )
