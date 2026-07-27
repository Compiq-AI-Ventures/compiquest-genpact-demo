"""Data aggregation for the compensation PDF report.

Fetches and aggregates Genpact F&A master-data for the caller's allowed
population, returning a :class:`ReportData` dataclass that the PDF builder
consumes. No LLM is involved — every figure is deterministic.

Reads ``genpact_employee_master`` (per-employee, per-fiscal-year rows) and
``genpact_benchmark`` (band percentiles by job family / level / currency).
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.genpact_master_data import GenpactBenchmark, GenpactEmployeeMaster

from .metrics import make_record
from .tracer import ReportTracer

# Statuses that indicate an employee has left the organisation. Genpact
# uses ``INACTIVE``; the others are kept for robustness across data sources.
_EXIT_STATUSES = {
    "INACTIVE", "Resigned", "Attrited", "Terminated", "Exited", "Separated", "Left",
}

# Key fields checked for data-quality completeness: (attr_name, display_label).
_QUALITY_FIELDS: list[tuple[str, str]] = [
    ("performance_rating", "Performance Rating"),
    ("variable_post", "Actual Variable Paid"),
    ("external_compa_post", "External Compa-Ratio"),
    ("internal_compa_post", "Internal Compa-Ratio"),
    ("lti_type", "LTI Type"),
    ("total_increment_pct", "Total Increment %"),
]


def _is_filled(row: GenpactEmployeeMaster, attr: str) -> bool:
    val = getattr(row, attr, None)
    if val is None:
        return False
    if isinstance(val, str):
        return bool(val.strip())
    return float(val) != 0.0


def _pct(num: int, denom: int) -> float:
    return round(num / denom * 100, 1) if denom else 0.0


def _avg(vals: list[float]) -> float | None:
    return round(sum(vals) / len(vals), 2) if vals else None


def _fmt_pct(v: float | None) -> str:
    return "N/A" if v is None else f"{v}%"


def _fmt_inr(v: int | float | None) -> str:
    return "N/A" if v is None else f"INR {int(v):,}"


# ---------------------------------------------------------------------------
# Report data container
# ---------------------------------------------------------------------------

@dataclass
class ReportData:
    fiscal_year: int
    headcount: int
    generated_at: str
    scope_label: str

    # Section 1 — Executive Summary
    avg_increment_pct: float | None = None
    total_variable_spend: int = 0
    correction_headcount: int = 0
    data_quality_pct: float = 0.0

    # Section 2 — Data Quality
    field_completeness: list[tuple[str, float]] = field(default_factory=list)
    withheld_sections: list[tuple[str, str]] = field(default_factory=list)

    # Section 3 — Retention vs Pay
    retention_available: bool = False
    increased_and_quit_count: int = 0
    increased_and_quit_pct: float = 0.0
    no_lift_count: int = 0
    no_lift_pct: float = 0.0

    # Section 4 — Spend & Movement
    effective_increment_pct: float | None = None
    variable_payout_actual: int = 0
    variable_payout_target: int = 0
    variable_payout_attainment_pct: float | None = None
    promotion_count: int = 0
    promotion_pct: float = 0.0

    # Section 5 — Corrections by Job Family
    corrections_available: bool = False
    corrections_by_family: list[dict] = field(default_factory=list)

    # Section 6 — Equity Participation
    lti_plans: list[dict] = field(default_factory=list)
    total_lti_participants: int = 0

    # Section 7 — Audit Trail
    audit_trail: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Population queries
# ---------------------------------------------------------------------------

async def resolve_fiscal_year(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Return the most recent fiscal year present in genpact_employee_master."""
    row = await db.execute(
        select(func.max(GenpactEmployeeMaster.fiscal_year)).where(
            GenpactEmployeeMaster.tenant_id == tenant_id
        )
    )
    fy = row.scalar_one_or_none()
    return int(fy) if fy else 2026


async def fetch_population(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    fiscal_year: int,
    caller_employee_id: str | None,
    is_org_wide: bool,
) -> list[GenpactEmployeeMaster]:
    """Fetch all master rows for the caller's allowed population.

    Org-wide roles get all rows for the tenant. Manager roles get a
    recursive subtree rooted at ``caller_employee_id``.
    """
    if is_org_wide or not caller_employee_id:
        result = await db.execute(
            select(GenpactEmployeeMaster).where(
                GenpactEmployeeMaster.tenant_id == tenant_id,
                GenpactEmployeeMaster.fiscal_year == str(fiscal_year),
            )
        )
        return list(result.scalars().all())

    # Recursive CTE: include all employees whose manager chain leads back
    # to caller_employee_id, at any depth.
    cte_sql = text("""
        WITH RECURSIVE subtree AS (
            SELECT employee_id
            FROM genpact_employee_master
            WHERE manager_employee_id = :mgr_id
              AND tenant_id = :tenant_id
              AND fiscal_year = :fy
            UNION ALL
            SELECT t.employee_id
            FROM genpact_employee_master t
            JOIN subtree s ON t.manager_employee_id = s.employee_id
            WHERE t.tenant_id = :tenant_id
              AND t.fiscal_year = :fy
        )
        SELECT employee_id FROM subtree
    """)
    ids_result = await db.execute(
        cte_sql,
        {"mgr_id": caller_employee_id, "tenant_id": tenant_id, "fy": str(fiscal_year)},
    )
    emp_ids = [r[0] for r in ids_result.fetchall()]
    if not emp_ids:
        return []

    result = await db.execute(
        select(GenpactEmployeeMaster).where(
            GenpactEmployeeMaster.tenant_id == tenant_id,
            GenpactEmployeeMaster.fiscal_year == str(fiscal_year),
            GenpactEmployeeMaster.employee_id.in_(emp_ids),
        )
    )
    return list(result.scalars().all())


async def fetch_population_by_emp_ids(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    fiscal_year: int,
    emp_ids: list[str],
) -> list[GenpactEmployeeMaster]:
    """Fetch master rows for a specific set of employee_ids."""
    if not emp_ids:
        return []
    result = await db.execute(
        select(GenpactEmployeeMaster).where(
            GenpactEmployeeMaster.tenant_id == tenant_id,
            GenpactEmployeeMaster.fiscal_year == str(fiscal_year),
            GenpactEmployeeMaster.employee_id.in_(emp_ids),
        )
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Main aggregation
# ---------------------------------------------------------------------------

async def build_report_data(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    fiscal_year: int,
    population: list[GenpactEmployeeMaster],
    caller_name: str,
    is_org_wide: bool,
    tracer: ReportTracer | None = None,
) -> ReportData:
    """Compute all seven report sections from the population rows."""
    n = len(population)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    data = ReportData(
        fiscal_year=fiscal_year,
        headcount=n,
        generated_at=now,
        scope_label="Full Organisation" if is_org_wide else f"Team: {caller_name}",
    )
    if tracer is not None:
        tracer.record_dataset(
            "genpact_employee_master",
            population,
            query_filter=f"tenant_id={tenant_id}, fiscal_year={fiscal_year}",
            fiscal_year=fiscal_year,
        )

    if n == 0:
        return data

    # --- Section 2: Data Quality (computed first; qualifies all below) ---
    completeness: list[tuple[str, float]] = []
    for attr, label in _QUALITY_FIELDS:
        filled = sum(1 for r in population if _is_filled(r, attr))
        completeness.append((label, _pct(filled, n)))
    data.field_completeness = completeness
    data.data_quality_pct = round(
        sum(p for _, p in completeness) / len(completeness), 1
    ) if completeness else 0.0

    if tracer is not None:
        tracer.register_metric(make_record("calc:data_quality_pct", data.data_quality_pct, _fmt_pct(data.data_quality_pct)))

    # --- Section 1: Executive Summary ---
    incr_vals = [float(r.total_increment_pct) for r in population if r.total_increment_pct]
    data.avg_increment_pct = _avg(incr_vals)
    data.total_variable_spend = int(sum(r.variable_post or 0 for r in population))
    # genpact_employee_master does not separate "correction" increases from
    # merit; corrections surface via the under-band/under-market analysis in
    # Section 5 instead.
    data.correction_headcount = 0

    if tracer is not None:
        tracer.register_metric(make_record("calc:headcount", n, str(n)))
        tracer.register_metric(make_record("calc:avg_increment_pct", data.avg_increment_pct, _fmt_pct(data.avg_increment_pct)))
        tracer.register_metric(make_record("calc:total_variable_spend", data.total_variable_spend, f"INR {data.total_variable_spend:,}"))
        tracer.register_metric(make_record("calc:correction_headcount", data.correction_headcount, str(data.correction_headcount)))

    # --- Section 3: Retention vs Pay ---
    exited = [
        r for r in population
        if r.status in _EXIT_STATUSES
        or (r.exit_classification and r.exit_classification.strip())
    ]
    if not exited:
        data.withheld_sections.append((
            "Retention vs Pay",
            "No exit/attrition records found in master data for this population.",
        ))
    else:
        data.retention_available = True
        increased_quit = [r for r in exited if float(r.base_increment_pct or 0) > 0]
        data.increased_and_quit_count = len(increased_quit)
        data.increased_and_quit_pct = _pct(len(increased_quit), n)

    no_lift = [r for r in population if not float(r.total_increment_pct or 0)]
    data.no_lift_count = len(no_lift)
    data.no_lift_pct = _pct(len(no_lift), n)

    if tracer is not None:
        tracer.register_metric(make_record(
            "calc:increased_and_quit_count",
            data.increased_and_quit_count if data.retention_available else None,
            str(data.increased_and_quit_count) if data.retention_available else "N/A",
        ))
        tracer.register_metric(make_record(
            "calc:increased_and_quit_pct",
            data.increased_and_quit_pct if data.retention_available else None,
            _fmt_pct(data.increased_and_quit_pct) if data.retention_available else "N/A",
        ))
        tracer.register_metric(make_record("calc:no_lift_count", data.no_lift_count, str(data.no_lift_count)))
        tracer.register_metric(make_record("calc:no_lift_pct", data.no_lift_pct, _fmt_pct(data.no_lift_pct)))

    # --- Section 4: Spend & Movement ---
    data.effective_increment_pct = data.avg_increment_pct
    data.variable_payout_actual = data.total_variable_spend
    # target_variable_pct is a fraction (e.g. 0.25 == 25%).
    target_total = int(sum(
        int(r.base_salary_post or 0) * float(r.target_variable_pct or 0)
        for r in population
    ))
    data.variable_payout_target = target_total
    if target_total:
        data.variable_payout_attainment_pct = round(
            data.total_variable_spend / target_total * 100, 1
        )
    data.promotion_count = sum(1 for r in population if r.promotion_flag)
    data.promotion_pct = _pct(data.promotion_count, n)

    if tracer is not None:
        tracer.register_metric(make_record("calc:effective_increment_pct", data.effective_increment_pct, _fmt_pct(data.effective_increment_pct)))
        tracer.register_metric(make_record("calc:variable_payout_actual", data.variable_payout_actual, f"INR {data.variable_payout_actual:,}"))
        tracer.register_metric(make_record("calc:variable_payout_target", target_total, f"INR {target_total:,}"))
        tracer.register_metric(make_record("calc:variable_payout_attainment_pct", data.variable_payout_attainment_pct, _fmt_pct(data.variable_payout_attainment_pct)))
        tracer.register_metric(make_record("calc:promotion_count", data.promotion_count, str(data.promotion_count)))
        tracer.register_metric(make_record("calc:promotion_pct", data.promotion_pct, _fmt_pct(data.promotion_pct)))

    # --- Section 5: Corrections by Job Family ---
    # Build band ranges from genpact_benchmark for the report fiscal year,
    # keyed by (job_family, job_level, currency). P25 is treated as the band
    # minimum, P50 as the market midpoint.
    bench_rows = list(
        (await db.execute(
            select(GenpactBenchmark).where(
                GenpactBenchmark.tenant_id == tenant_id,
                GenpactBenchmark.survey_year == fiscal_year,
            )
        )).scalars().all()
    )
    _acc: dict[tuple[str, str, str], list[tuple[int, int]]] = defaultdict(list)
    for b in bench_rows:
        key = ((b.job_family or "").lower(), (b.job_level or "").lower(), (b.currency or "").upper())
        _acc[key].append((int(b.base_p25 or 0), int(b.base_p50 or 0)))
    policies: dict[tuple[str, str, str], dict] = {
        k: {
            "min": round(sum(p25 for p25, _ in v) / len(v)),
            "p50": round(sum(p50 for _, p50 in v) / len(v)),
        }
        for k, v in _acc.items()
    }

    if not policies:
        data.withheld_sections.append((
            "Corrections by Job Family",
            "No benchmark (band range) data loaded for this tenant.",
        ))
    else:
        data.corrections_available = True

        def _empty_stats() -> dict:
            return {"under_band": 0, "under_market": 0, "cost_to_close": 0, "headcount": 0}

        fam_stats: dict[str, dict] = defaultdict(_empty_stats)
        for r in population:
            fam = r.job_family or ""
            s = fam_stats[fam]
            s["headcount"] += 1
            policy = policies.get(
                (fam.lower(), (r.job_level or "").lower(), (r.currency or "").upper())
            )
            base = int(r.base_salary_post or 0)
            if policy and base < policy["min"]:
                s["under_band"] += 1
                s["cost_to_close"] += policy["min"] - base
            if float(r.external_compa_post or 0) < 0.95:
                s["under_market"] += 1
                if policy and base < policy["p50"]:
                    s["cost_to_close"] += policy["p50"] - base

        data.corrections_by_family = sorted(
            [
                {
                    "job_family": fam,
                    "headcount": s["headcount"],
                    "under_band": s["under_band"],
                    "under_market": s["under_market"],
                    "cost_to_close": s["cost_to_close"],
                    "benchmark_vintage": f"Genpact benchmark FY{fiscal_year}",
                }
                for fam, s in fam_stats.items()
                if s["under_band"] or s["under_market"]
            ],
            key=lambda x: x["cost_to_close"],
            reverse=True,
        )

    # --- Section 6: Equity Participation ---
    eligible = [r for r in population if r.lti_eligible]
    data.total_lti_participants = len(eligible)
    plan_counts = Counter(r.lti_type or "Unknown" for r in eligible)
    data.lti_plans = [
        {"lti_type": k, "participant_count": v}
        for k, v in sorted(plan_counts.items(), key=lambda x: x[1], reverse=True)
    ]

    if tracer is not None:
        tracer.register_metric(make_record("calc:total_lti_participants", data.total_lti_participants, str(data.total_lti_participants)))

    # --- Section 7: Audit Trail ---
    data.audit_trail = [
        {
            "step": 1,
            "function": "fetch_population",
            "inputs": f"tenant={tenant_id}, fy={fiscal_year}, org_wide={is_org_wide}",
            "output": f"{n} rows fetched",
            "timestamp": now,
        },
        {
            "step": 2,
            "function": "data_quality_check",
            "inputs": f"{len(_QUALITY_FIELDS)} fields checked",
            "output": f"completeness {data.data_quality_pct}%",
            "timestamp": now,
        },
        {
            "step": 3,
            "function": "retention_vs_pay",
            "inputs": "exit status / exit_classification filter",
            "output": f"exited={len(exited)}, increased_and_quit={data.increased_and_quit_count}",
            "timestamp": now,
        },
        {
            "step": 4,
            "function": "spend_and_movement",
            "inputs": f"headcount={n}",
            "output": f"avg_increment={data.avg_increment_pct}%, promotions={data.promotion_count}",
            "timestamp": now,
        },
        {
            "step": 5,
            "function": "corrections_by_job_family",
            "inputs": f"policy_entries={len(policies)}, population={n}",
            "output": f"{len(data.corrections_by_family)} families with corrections",
            "timestamp": now,
        },
        {
            "step": 6,
            "function": "equity_participation",
            "inputs": "lti_eligible=True filter",
            "output": f"participants={data.total_lti_participants}, plans={len(data.lti_plans)}",
            "timestamp": now,
        },
    ]

    return data
