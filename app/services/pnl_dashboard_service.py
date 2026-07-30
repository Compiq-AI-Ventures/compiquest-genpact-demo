"""P&L Head Executive Summary — org-wide read aggregation.

Reproduces ``Genpact_Executive_Summary.xlsx`` exactly from the tables
already backing the rest of the app. Every formula below was verified
against the live seeded DB before being written here; see the plan
doc for the full reconciliation notes.

Org-wide, not BU-scoped: every PNL_HEAD caller sees the same figures.
No manager/subtree scoping — much simpler than the MoM/Manager
services in ``jvre_workspace_service.py``.

Two data sources:

* ``jvre_snapshots`` / ``genpact_employee_master`` — 8 of 9 metrics.
* ``genpact_job_posting`` — backfill cost + New Hire Median Cost. That
  table (and ``genpact_currency_master``) has no ORM class (see
  ``app/models/genpact_master_data.py``), so both are queried via
  plain ``text()`` SQL rather than the Core ``Table``/ORM layer.

FX conversion for the two genpact_job_posting-derived metrics
(backfill cost, median cost) needs each leaver's currency, which
varies per row — that's a per-row SQL join against
``genpact_currency_master``, not the single-FY rate table used by
``compchat/tools.py``. Plain ``text()`` queries, consistent with that
module's convention for reading genpact_* master data.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts import build_pnl_bullets_prompt
from app.core.config import get_settings
from app.models.genpact_master_data import GenpactEmployeeMaster
from app.models.jvre_snapshot import JvreSnapshot
from app.schemas.pnl_dashboard_schema import PnlExecutiveSummaryResponse
from app.services.iquest_streaming_service import invoke_llm_sync

logger = logging.getLogger(__name__)

# A real (non-trivial) prompt takes the local Ollama SLM ~6-7s to generate —
# leave headroom over that before giving up and falling back to the
# deterministic template.
_BULLETS_LLM_TIMEOUT_SECONDS = 15.0

# Bullets are cached per-tenant per-facts-hash so repeat dashboard loads with
# unchanged KPI data don't re-pay SLM latency on every request — but the
# cache is TTL-bound, not indefinite, so the wording stays LLM-generated and
# fresh (re-phrased) periodically rather than freezing on the first-ever
# generation for the life of the process.
_BULLETS_CACHE_TTL_SECONDS = 600.0
_bullets_cache: dict[tuple[uuid.UUID, str], tuple[list[str], float]] = {}


def _facts_cache_key(tenant_id: uuid.UUID, facts: dict[str, str]) -> tuple[uuid.UUID, str]:
    digest = hashlib.sha256(
        json.dumps(facts, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return (tenant_id, digest)

# Reviewed-employee filter for the "actual historical increment" figure —
# unreviewed rows (performance_rating == 0) skew the average toward the
# JVRE-recommended increment instead of what was actually awarded.
_REVIEWED_FILTER = GenpactEmployeeMaster.__table__.c.performance_rating > 0

_LEADERSHIP_LEVELS = ("1", "2", "3")

# genpact_employee_master carries one row per employee per fiscal year
# (FY2010 through FY2026) — "current" figures (compa-ratio, increment%)
# must be scoped to the active FY, or the average silently blends in
# a decade of historical rows.
_CURRENT_FY = "2026"

# Leaver's most-recent master-data row, regardless of which FY they left
# in — reused for both genpact_job_posting-derived metrics below.
_LEAVER_FX_CTE = """
WITH leaver_fx AS (
    SELECT DISTINCT ON (employee_id) employee_id, currency, fiscal_year
    FROM genpact_employee_master
    WHERE tenant_id = :tenant_id
    ORDER BY employee_id, fiscal_year DESC
)
"""


async def get_executive_summary(
    db: AsyncSession, tenant_id: uuid.UUID
) -> PnlExecutiveSummaryResponse:
    beginning_base_cost, beginning_headcount = await _base_cost(db, tenant_id)
    projected_new_base = await _sum_recommended_base(db, tenant_id)
    new_hire_total_cost = await _sum_recommended_tcc(db, tenant_id)
    external_compa, internal_compa = await _compa_ratios(db, tenant_id)
    increment_pct = await _increment_pct(db, tenant_id)
    attrition = await _attrition_by_fy(db, tenant_id)
    leadership = await _leadership_retention_by_fy(db, tenant_id)
    backfill_cost, backfill_count = await _backfill_cost(db, tenant_id)
    median_cost = await _new_hire_median_cost(db, tenant_id)

    bullet_kwargs = {
        "beginning_base_cost": beginning_base_cost,
        "attrition_fy26": attrition.get("2026", 0.0),
        "attrition_fy24": attrition.get("2024", 0.0),
        "leadership_fy26": leadership[1],
        "new_hire_total_cost": new_hire_total_cost,
        "backfill_hire_count": backfill_count,
        "backfill_cost": backfill_cost,
        "external_compa": external_compa,
        "increment_pct": increment_pct,
    }
    bullets = await _generate_bullets(tenant_id, **bullet_kwargs)

    return PnlExecutiveSummaryResponse(
        beginning_base_cost=beginning_base_cost,
        beginning_headcount=beginning_headcount,
        increment_pct=increment_pct,
        new_hire_total_cost=new_hire_total_cost,
        projected_new_base=projected_new_base,
        external_compa_ratio=external_compa,
        internal_compa_ratio=internal_compa,
        attrition_rate_fy24=attrition.get("2024", 0.0),
        attrition_rate_fy25=attrition.get("2025", 0.0),
        attrition_rate_fy26=attrition.get("2026", 0.0),
        leadership_retention_fy24=leadership[0],
        leadership_retention_fy26=leadership[1],
        leadership_headcount=leadership[2],
        backfill_cost=backfill_cost,
        backfill_hire_count=backfill_count,
        new_hire_median_cost=median_cost,
        bullets=bullets,
    )


async def _base_cost(db: AsyncSession, tenant_id: uuid.UUID) -> tuple[Decimal, int]:
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(JvreSnapshot.current_base), 0),
                func.count(),
            ).where(JvreSnapshot.tenant_id == tenant_id)
        )
    ).one()
    return Decimal(row[0]), int(row[1])


async def _sum_recommended_base(db: AsyncSession, tenant_id: uuid.UUID) -> Decimal:
    total = (
        await db.execute(
            select(func.coalesce(func.sum(JvreSnapshot.recommended_base), 0)).where(
                JvreSnapshot.tenant_id == tenant_id
            )
        )
    ).scalar_one()
    return Decimal(total)


async def _sum_recommended_tcc(db: AsyncSession, tenant_id: uuid.UUID) -> Decimal:
    total = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(JvreSnapshot.recommended_base + JvreSnapshot.recommended_variable),
                    0,
                )
            ).where(JvreSnapshot.tenant_id == tenant_id)
        )
    ).scalar_one()
    return Decimal(total)


async def _compa_ratios(db: AsyncSession, tenant_id: uuid.UUID) -> tuple[float, float]:
    tbl = GenpactEmployeeMaster.__table__
    row = (
        await db.execute(
            select(
                func.avg(tbl.c.external_compa_pre),
                func.avg(tbl.c.internal_compa_pre),
            ).where(tbl.c.tenant_id == tenant_id, tbl.c.fiscal_year == _CURRENT_FY)
        )
    ).one()
    return float(row[0] or 0), float(row[1] or 0)


async def _increment_pct(db: AsyncSession, tenant_id: uuid.UUID) -> float:
    """Average of ``base_increment_pct`` (stored as a fraction, e.g. 0.06) for
    FY2026 rows with an actual performance review — scaled to a percentage."""
    tbl = GenpactEmployeeMaster.__table__
    avg = (
        await db.execute(
            select(func.avg(tbl.c.base_increment_pct)).where(
                tbl.c.tenant_id == tenant_id,
                tbl.c.fiscal_year == _CURRENT_FY,
                _REVIEWED_FILTER,
            )
        )
    ).scalar_one()
    return float(avg or 0) * 100.0


async def _attrition_by_fy(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, float]:
    tbl = GenpactEmployeeMaster.__table__
    rows = (
        await db.execute(
            select(
                tbl.c.fiscal_year,
                func.count().filter(tbl.c.status == "INACTIVE"),
                func.count(),
            )
            .where(tbl.c.tenant_id == tenant_id)
            .group_by(tbl.c.fiscal_year)
        )
    ).all()
    return {
        fy: (inactive / total * 100.0 if total else 0.0) for fy, inactive, total in rows
    }


async def _leadership_retention_by_fy(
    db: AsyncSession, tenant_id: uuid.UUID
) -> tuple[float, float, int]:
    """Returns (retention_fy24, retention_fy26, headcount) for job_level 1-3."""
    tbl = GenpactEmployeeMaster.__table__
    leadership_filter = tbl.c.job_level.in_(_LEADERSHIP_LEVELS)
    rows = (
        await db.execute(
            select(
                tbl.c.fiscal_year,
                func.count().filter(tbl.c.status != "INACTIVE"),
                func.count(),
            )
            .where(tbl.c.tenant_id == tenant_id, leadership_filter)
            .group_by(tbl.c.fiscal_year)
        )
    ).all()
    by_fy = {fy: (active, total) for fy, active, total in rows}
    active_24, total_24 = by_fy.get("2024", (0, 0))
    active_26, total_26 = by_fy.get("2026", (0, 0))
    retention_24 = active_24 / total_24 * 100.0 if total_24 else 0.0
    retention_26 = active_26 / total_26 * 100.0 if total_26 else 0.0
    return retention_24, retention_26, total_26


async def _backfill_cost(db: AsyncSession, tenant_id: uuid.UUID) -> tuple[Decimal, int]:
    """Backfill cost is only incurred for offers that actually converted to a
    hire — Declined/Rescinded postings never triggered agency/recruiter/
    onboarding spend, so they're excluded (same population as the median-cost
    query below)."""
    row = (
        await db.execute(
            sql_text(
                _LEAVER_FX_CTE
                + """
                SELECT
                    count(*),
                    coalesce(sum(
                        (jp.agency_fee_paid + jp.recruiter_cost_estimate + jp.onboarding_cost)
                        / cm.conversion_value
                    ), 0)
                FROM genpact_job_posting jp
                JOIN leaver_fx lf ON lf.employee_id = jp.leaver_employee_id
                JOIN genpact_currency_master cm
                    ON cm.local_currency = lf.currency
                    AND cm.reporting_cycle = lf.fiscal_year
                    AND cm.tenant_id = :tenant_id
                WHERE jp.tenant_id = :tenant_id
                    AND jp.offer_outcome = 'Accepted'
                """
            ),
            {"tenant_id": str(tenant_id)},
        )
    ).one()
    return Decimal(str(row[1])), int(row[0])


async def _new_hire_median_cost(db: AsyncSession, tenant_id: uuid.UUID) -> Decimal:
    """Median (new_hire_base_salary - leaver_base_salary), USD, Accepted offers only.

    Declined/Rescinded offers never produced a real hire — their
    "new hire" salary is hypothetical, not an actually-paid figure.
    """
    value = (
        await db.execute(
            sql_text(
                _LEAVER_FX_CTE
                + """
                SELECT percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY jp.salary_premium / cm.conversion_value
                )
                FROM genpact_job_posting jp
                JOIN leaver_fx lf ON lf.employee_id = jp.leaver_employee_id
                JOIN genpact_currency_master cm
                    ON cm.local_currency = lf.currency
                    AND cm.reporting_cycle = lf.fiscal_year
                    AND cm.tenant_id = :tenant_id
                WHERE jp.tenant_id = :tenant_id
                    AND jp.offer_outcome = 'Accepted'
                """
            ),
            {"tenant_id": str(tenant_id)},
        )
    ).scalar_one()
    return Decimal(str(value or 0))


def _bullet_facts(
    *,
    beginning_base_cost: Decimal,
    attrition_fy26: float,
    attrition_fy24: float,
    leadership_fy26: float,
    new_hire_total_cost: Decimal,
    backfill_hire_count: int,
    backfill_cost: Decimal,
    external_compa: float,
    increment_pct: float,
) -> dict[str, str]:
    """Pre-formatted (already-rounded) display strings — the only numbers
    either the template or the LLM is allowed to use verbatim."""
    attrition_delta = attrition_fy26 - attrition_fy24
    return {
        "FY2026 base compensation spend": f"${beginning_base_cost / 1_000_000:.1f}M",
        "FY2026 attrition rate": f"{attrition_fy26:.1f}%",
        "Attrition change vs FY24": f"{'up' if attrition_delta >= 0 else 'down'} {abs(attrition_delta):.1f} points",
        "Leadership retention (job level 1-3)": f"{leadership_fy26:.1f}%",
        "New hire total cost": f"${new_hire_total_cost / 1_000_000:.1f}M",
        "Backfill cost": f"${backfill_cost / 1_000_000:.2f}M",
        "Number of backfill hires": f"{backfill_hire_count:,}",
        "External compa-ratio": f"{external_compa:.2f}x",
        "Average increment for reviewed employees": f"{increment_pct:.1f}%",
    }


def _build_bullets(**kwargs) -> list[str]:
    """Deterministic fallback bullets — used whenever the LLM path fails."""
    facts = _bullet_facts(**kwargs)
    return [
        f"FY2026 base compensation spend is {facts['FY2026 base compensation spend']}. "
        f"Attrition rate was {facts['FY2026 attrition rate']}, "
        f"{facts['Attrition change vs FY24']} vs FY24.",
        f"Leadership retention stood at {facts['Leadership retention (job level 1-3)']}. "
        f"New hire cost reached {facts['New hire total cost']}, "
        f"with backfill at {facts['Backfill cost']} across {facts['Number of backfill hires']} hires.",
        f"External compa-ratio is {facts['External compa-ratio']}.",
        f"Average increment for reviewed employees was {facts['Average increment for reviewed employees']}.",
        "Focus on aligning compensation with business outcomes is crucial for FY2027 success.",
    ]


def _parse_bullets_from_llm(raw: str) -> list[str]:
    """Parse a JSON array of strings, tolerating markdown fences. Falls back
    to a plain-line scan if JSON parsing fails. Raises ValueError if fewer
    than 5 usable bullets come out either way, so the caller can fall back
    to the deterministic template."""
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    try:
        bullets = json.loads(cleaned)
        if not isinstance(bullets, list):
            raise ValueError("LLM output was not a JSON array")
        bullets = [str(b).strip() for b in bullets if str(b).strip()]
    except json.JSONDecodeError:
        bullets = [line.strip("-• ").strip() for line in cleaned.splitlines() if line.strip()]

    if len(bullets) < 5:
        raise ValueError(f"LLM returned {len(bullets)} usable bullets, need 5")
    return bullets[:5]


async def _generate_bullets(tenant_id: uuid.UUID, **kwargs) -> list[str]:
    """Try the SLM for natural-language bullets; fall back to the
    deterministic template on any failure (timeout, unreachable model,
    malformed output) so the dashboard never blocks on this.

    Cached per-tenant on the exact facts fed to the model, with a TTL — this
    keeps repeat dashboard loads fast without paying SLM latency every time,
    while still re-generating (re-phrasing) periodically instead of freezing
    on the first-ever LLM output for the life of the process.
    """
    facts = _bullet_facts(**kwargs)
    cache_key = _facts_cache_key(tenant_id, facts)
    cached = _bullets_cache.get(cache_key)
    if cached is not None:
        bullets, cached_at = cached
        if time.monotonic() - cached_at < _BULLETS_CACHE_TTL_SECONDS:
            return bullets

    try:
        settings = get_settings()
        prompt = build_pnl_bullets_prompt(facts)
        loop = asyncio.get_running_loop()
        raw = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: invoke_llm_sync(
                    settings, [{"role": "user", "content": prompt}],
                    max_tokens=400, temperature=0.3,
                ),
            ),
            timeout=_BULLETS_LLM_TIMEOUT_SECONDS,
        )
        bullets = _parse_bullets_from_llm(raw)
    except Exception:
        logger.warning("pnl_bullets.llm_failed; falling back to template", exc_info=True)
        bullets = _build_bullets(**kwargs)

    _bullets_cache[cache_key] = (bullets, time.monotonic())
    return bullets
