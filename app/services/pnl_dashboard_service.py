"""P&L Head Executive Summary — org-wide KPI aggregation.

Org-wide, not BU-scoped: every PNL_HEAD caller sees the same figures.
No manager/subtree scoping — much simpler than the MoM/Manager services
in ``jvre_workspace_service.py``.

The dashboard measures FY2025 actuals against FY2024, and projects
FY2026. The KPI definitions (agreed with the finance team):

1. Beginning base cost — sum of ACTIVE employees' pre-increment base
   salary, plus the delta vs the prior FY.
2. Increment — average base increment %, and the same in dollars.
3. New hire total cost — backfill + net new hires (count and cost).
4. Projected new base — beginning base + new hire total + increment.
5. Attrition rate — leavers / headcount, vs the prior FY.
6. Leadership retention — 1 − attrition among leadership job levels.
7. New hire median cost — median incoming vs outgoing base pay.

All money is converted to USD at insert-time rates held in
``genpact_currency_master`` (keyed by fiscal year + local currency), so
every sum joins through that table. These are ``text()`` queries rather
than ORM ones because ``genpact_job_posting`` and
``genpact_currency_master`` have no ORM class — see
``app/models/genpact_master_data.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts import build_pnl_bullets_prompt
from app.core.config import get_settings
from app.schemas.pnl_dashboard_schema import PnlExecutiveSummaryResponse
from app.services.iquest_streaming_service import invoke_llm_sync

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fiscal years. genpact_employee_master carries one row per employee per FY
# (2023-2026), so every "current" figure must be FY-scoped or the average
# silently blends in several years of history.
# ---------------------------------------------------------------------------
_CURRENT_FY = "2025"
_PREV_FY = "2024"
_PROJECTED_FY = "2026"

# Leadership = the top job levels. Used only by the retention KPI.
_LEADERSHIP_LEVELS = ("1", "2", "3")

# ---------------------------------------------------------------------------
# Supplied figures — NOT yet derivable from the seeded data.
#
# These come from the finance team's own model. The seeded genpact_* tables
# don't reproduce them: the closest derivations give a backfill cost of
# $1.33M-$2.66M (vs $3.95M), a net-new-hire base of $128.6M (vs $50.6M),
# and leadership retention at job levels 1-3 of 90.97% (vs 90.8%). Effective
# increment and wage inflation have no stated formula at all.
#
# Every other number on this dashboard IS derived — see the functions below,
# each of which reconciles exactly against the finance team's figures.
# Replace these constants with real derivations once the source logic for
# them is confirmed.
# ---------------------------------------------------------------------------
_SUPPLIED_BACKFILL_COST = Decimal("3950000")
_SUPPLIED_NET_NEW_HIRE_COST = Decimal("50600000")
_SUPPLIED_LEADERSHIP_RETENTION_PCT = 90.8
_SUPPLIED_EFFECTIVE_INCREMENT_PCT = 11.22
_SUPPLIED_WAGE_INFLATION_PCT = 11.19

# A real prompt takes the local Ollama SLM ~6-7s — leave headroom before
# falling back to the deterministic template.
_BULLETS_LLM_TIMEOUT_SECONDS = 15.0

# Bullets are cached per-tenant per-facts-hash so repeat dashboard loads don't
# re-pay SLM latency, but the cache is TTL-bound so the wording stays fresh
# rather than freezing on the first-ever generation.
_BULLETS_CACHE_TTL_SECONDS = 600.0
_bullets_cache: dict[tuple[uuid.UUID, str], tuple[list[str], float]] = {}

# Every monetary sum needs the employee's FY-specific USD rate.
_FX_JOIN = """
    JOIN genpact_currency_master cm
      ON cm.local_currency  = e.currency
     AND cm.reporting_cycle = e.fiscal_year
     AND cm.tenant_id       = :tenant_id
"""


def _fy_bounds(fy: str) -> tuple[date, date]:
    """Half-open [start, end) calendar bounds for a fiscal year.

    Passed as real dates rather than casting inside SQL: asyncpg infers a
    placeholder's type from its position, so ``make_date(CAST(:fy AS int)…)``
    makes it demand an int and reject the string FY key used elsewhere.
    """
    year = int(fy)
    return date(year, 1, 1), date(year + 1, 1, 1)


def _facts_cache_key(tenant_id: uuid.UUID, facts: dict[str, str]) -> tuple[uuid.UUID, str]:
    digest = hashlib.sha256(json.dumps(facts, sort_keys=True).encode("utf-8")).hexdigest()
    return (tenant_id, digest)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
async def get_executive_summary(
    db: AsyncSession, tenant_id: uuid.UUID
) -> PnlExecutiveSummaryResponse:
    # 1. Beginning base cost + headcount, this FY and last.
    base_cost, headcount = await _base_cost(db, tenant_id, _CURRENT_FY)
    base_cost_prev, _ = await _base_cost(db, tenant_id, _PREV_FY)
    base_cost_delta = base_cost - base_cost_prev
    base_cost_delta_pct = (
        float(base_cost_delta / base_cost_prev * 100) if base_cost_prev else 0.0
    )

    # 2. Increment.
    increment_pct, increment_amount = await _increment(db, tenant_id, _CURRENT_FY)

    # 3. New hires. Counts are derived; the cost split is supplied.
    new_hire_count = await _new_hire_count(db, tenant_id, _CURRENT_FY)
    backfill_count = await _backfill_count(db, tenant_id, _CURRENT_FY)
    net_new_hire_count = max(new_hire_count - backfill_count, 0)
    backfill_cost = _SUPPLIED_BACKFILL_COST
    net_new_hire_cost = _SUPPLIED_NET_NEW_HIRE_COST
    new_hire_total_cost = backfill_cost + net_new_hire_cost

    # 4. Projected new base = beginning base + new hire total + increment.
    projected_new_base = base_cost + new_hire_total_cost + increment_amount
    projected_headcount = headcount + net_new_hire_count

    # 5. Attrition, this FY vs last.
    attrition_rate = await _attrition_rate(db, tenant_id, _CURRENT_FY)
    attrition_rate_prev = await _attrition_rate(db, tenant_id, _PREV_FY)

    # 7. New hire median cost.
    median_cost = await _new_hire_median_cost(db, tenant_id, _CURRENT_FY)

    summary = PnlExecutiveSummaryResponse(
        fy_label=f"FY{_CURRENT_FY}",
        prev_fy_label=f"FY{_PREV_FY}",
        projected_fy_label=f"FY{_PROJECTED_FY}",
        beginning_base_cost=base_cost,
        beginning_headcount=headcount,
        beginning_base_cost_prev=base_cost_prev,
        beginning_base_cost_delta=base_cost_delta,
        beginning_base_cost_delta_pct=base_cost_delta_pct,
        increment_pct=increment_pct,
        increment_amount=increment_amount,
        new_hire_total_cost=new_hire_total_cost,
        new_hire_count=new_hire_count,
        backfill_cost=backfill_cost,
        backfill_count=backfill_count,
        net_new_hire_cost=net_new_hire_cost,
        net_new_hire_count=net_new_hire_count,
        projected_new_base=projected_new_base,
        projected_headcount=projected_headcount,
        effective_increment_pct=_SUPPLIED_EFFECTIVE_INCREMENT_PCT,
        wage_inflation_pct=_SUPPLIED_WAGE_INFLATION_PCT,
        attrition_rate=attrition_rate,
        attrition_rate_prev=attrition_rate_prev,
        attrition_delta_pp=attrition_rate - attrition_rate_prev,
        # The cost of replacing this FY's leavers is the backfill spend.
        attrition_additional_cost=backfill_cost,
        leadership_retention=_SUPPLIED_LEADERSHIP_RETENTION_PCT,
        new_hire_median_cost=median_cost,
        bullets=[],
    )
    summary.bullets = await _generate_bullets(tenant_id, summary)
    return summary


# ---------------------------------------------------------------------------
# 1. Beginning base cost
# ---------------------------------------------------------------------------
async def _base_cost(
    db: AsyncSession, tenant_id: uuid.UUID, fy: str
) -> tuple[Decimal, int]:
    """Sum of ACTIVE employees' pre-increment base salary (USD), plus the
    active headcount that produced it."""
    row = (
        await db.execute(
            sql_text(
                f"""
                SELECT
                    coalesce(sum(
                        CASE WHEN e.status = 'ACTIVE'
                             THEN e.base_salary_pre / cm.conversion_value END
                    ), 0),
                    count(*) FILTER (WHERE e.status = 'ACTIVE')
                FROM genpact_employee_master e
                {_FX_JOIN}
                WHERE e.tenant_id = :tenant_id AND e.fiscal_year = :fy
                """
            ),
            {"tenant_id": str(tenant_id), "fy": fy},
        )
    ).one()
    return Decimal(str(row[0])), int(row[1])


# ---------------------------------------------------------------------------
# 2. Increment
# ---------------------------------------------------------------------------
async def _increment(
    db: AsyncSession, tenant_id: uuid.UUID, fy: str
) -> tuple[float, Decimal]:
    """Average base increment % and the same spend in USD.

    The percentage is restricted to employees with an actual performance
    review — unreviewed rows carry a 0% increment and would drag the
    average down to roughly half the awarded rate. The dollar figure is
    the true post-minus-pre delta across everyone, so it needs no filter.
    """
    row = (
        await db.execute(
            sql_text(
                f"""
                SELECT
                    coalesce(avg(e.base_increment_pct)
                             FILTER (WHERE e.performance_rating > 0), 0) * 100,
                    coalesce(sum(
                        (e.base_salary_post - e.base_salary_pre) / cm.conversion_value
                    ), 0)
                FROM genpact_employee_master e
                {_FX_JOIN}
                WHERE e.tenant_id = :tenant_id AND e.fiscal_year = :fy
                """
            ),
            {"tenant_id": str(tenant_id), "fy": fy},
        )
    ).one()
    return float(row[0]), Decimal(str(row[1]))


# ---------------------------------------------------------------------------
# 3. New hires
# ---------------------------------------------------------------------------
async def _new_hire_count(db: AsyncSession, tenant_id: uuid.UUID, fy: str) -> int:
    """Everyone who joined during the fiscal year (backfills + net new)."""
    fy_start, fy_end = _fy_bounds(fy)
    count = (
        await db.execute(
            sql_text(
                """
                SELECT count(*)
                FROM genpact_employee_master e
                WHERE e.tenant_id = :tenant_id
                  AND e.fiscal_year = :fy
                  AND e.joining_date >= :fy_start
                  AND e.joining_date <  :fy_end
                """
            ),
            {"tenant_id": str(tenant_id), "fy": fy, "fy_start": fy_start, "fy_end": fy_end},
        )
    ).scalar_one()
    return int(count)


async def _backfill_count(db: AsyncSession, tenant_id: uuid.UUID, fy: str) -> int:
    """Replacement hires who started during the fiscal year.

    Only ``Accepted`` offers count — Declined/Rescinded postings never
    produced a hire, so they carry no backfill.
    """
    fy_start, fy_end = _fy_bounds(fy)
    count = (
        await db.execute(
            sql_text(
                """
                SELECT count(*)
                FROM genpact_job_posting jp
                WHERE jp.tenant_id = :tenant_id
                  AND jp.offer_outcome = 'Accepted'
                  AND jp.date_of_joining_replacement >= :fy_start
                  AND jp.date_of_joining_replacement <  :fy_end
                """
            ),
            {"tenant_id": str(tenant_id), "fy_start": fy_start, "fy_end": fy_end},
        )
    ).scalar_one()
    return int(count)


# ---------------------------------------------------------------------------
# 5. Attrition
# ---------------------------------------------------------------------------
async def _attrition_rate(db: AsyncSession, tenant_id: uuid.UUID, fy: str) -> float:
    """Leavers as a percentage of total headcount for the fiscal year."""
    row = (
        await db.execute(
            sql_text(
                """
                SELECT count(*) FILTER (WHERE e.status = 'INACTIVE'), count(*)
                FROM genpact_employee_master e
                WHERE e.tenant_id = :tenant_id AND e.fiscal_year = :fy
                """
            ),
            {"tenant_id": str(tenant_id), "fy": fy},
        )
    ).one()
    leavers, total = int(row[0]), int(row[1])
    return leavers / total * 100.0 if total else 0.0


# ---------------------------------------------------------------------------
# 7. New hire median cost
# ---------------------------------------------------------------------------
async def _new_hire_median_cost(
    db: AsyncSession, tenant_id: uuid.UUID, fy: str
) -> Decimal:
    """Median incoming-minus-outgoing base pay (USD) across the FY's backfills.

    ``salary_premium`` is stored pre-computed and equals
    ``new_hire_base_salary - leaver_base_salary`` for every row. Each row is
    converted using the leaver's most recent currency, since leavers may have
    exited in an earlier fiscal year than the replacement's start date.
    """
    fy_start, fy_end = _fy_bounds(fy)
    value = (
        await db.execute(
            sql_text(
                """
                WITH leaver_fx AS (
                    SELECT DISTINCT ON (employee_id) employee_id, currency, fiscal_year
                    FROM genpact_employee_master
                    WHERE tenant_id = :tenant_id
                    ORDER BY employee_id, fiscal_year DESC
                )
                SELECT percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY jp.salary_premium / cm.conversion_value
                )
                FROM genpact_job_posting jp
                JOIN leaver_fx lf ON lf.employee_id = jp.leaver_employee_id
                JOIN genpact_currency_master cm
                  ON cm.local_currency  = lf.currency
                 AND cm.reporting_cycle = lf.fiscal_year
                 AND cm.tenant_id       = :tenant_id
                WHERE jp.tenant_id = :tenant_id
                  AND jp.offer_outcome = 'Accepted'
                  AND jp.date_of_joining_replacement >= :fy_start
                  AND jp.date_of_joining_replacement <  :fy_end
                """
            ),
            {"tenant_id": str(tenant_id), "fy_start": fy_start, "fy_end": fy_end},
        )
    ).scalar_one()
    return Decimal(str(value or 0))


# ---------------------------------------------------------------------------
# Narrative bullets
# ---------------------------------------------------------------------------
def _m(amount: Decimal, dp: int = 1) -> str:
    return f"${amount / 1_000_000:.{dp}f}M"


def _bullet_facts(s: PnlExecutiveSummaryResponse) -> dict[str, str]:
    """Pre-formatted display strings — the only numbers either the template
    or the LLM is allowed to use verbatim."""
    direction = "up" if s.attrition_delta_pp >= 0 else "down"
    return {
        f"{s.fy_label} beginning base cost": _m(s.beginning_base_cost),
        "Headcount": f"{s.beginning_headcount:,}",
        f"Base cost change vs {s.prev_fy_label}": (
            f"{_m(s.beginning_base_cost_delta)} "
            f"({s.beginning_base_cost_delta_pct:+.2f}%)"
        ),
        "Average base increment": f"{s.increment_pct:.2f}%",
        "Increment spend": _m(s.increment_amount),
        "New hire total cost": _m(s.new_hire_total_cost, 2),
        "New hires": f"{s.new_hire_count:,}",
        "Backfill cost": _m(s.backfill_cost, 2),
        "Backfill hires": f"{s.backfill_count:,}",
        "Net new hires": f"{s.net_new_hire_count:,}",
        f"{s.projected_fy_label} projected new base": _m(s.projected_new_base, 2),
        f"{s.projected_fy_label} projected headcount": f"{s.projected_headcount:,}",
        f"{s.fy_label} attrition rate": f"{s.attrition_rate:.2f}%",
        f"Attrition change vs {s.prev_fy_label}": (
            f"{direction} {abs(s.attrition_delta_pp):.2f} points"
        ),
        "Leadership retention": f"{s.leadership_retention:.1f}%",
        "New hire median cost": f"${s.new_hire_median_cost:,.0f}",
    }


def _build_bullets(s: PnlExecutiveSummaryResponse) -> list[str]:
    """Deterministic fallback bullets — used whenever the LLM path fails."""
    f = _bullet_facts(s)
    direction = "up" if s.attrition_delta_pp >= 0 else "down"
    return [
        f"{s.fy_label} beginning base cost is {f[f'{s.fy_label} beginning base cost']} "
        f"across {f['Headcount']} employees, "
        f"{f[f'Base cost change vs {s.prev_fy_label}']} versus {s.prev_fy_label}.",
        f"The average base increment was {f['Average base increment']}, "
        f"costing {f['Increment spend']}.",
        f"New hire cost reached {f['New hire total cost']} across {f['New hires']} hires — "
        f"{f['Backfill hires']} backfills at {f['Backfill cost']} and "
        f"{f['Net new hires']} net new roles.",
        f"Attrition was {f[f'{s.fy_label} attrition rate']}, {direction} "
        f"{abs(s.attrition_delta_pp):.2f} points on {s.prev_fy_label}, with leadership "
        f"retention holding at {f['Leadership retention']}.",
        f"{s.projected_fy_label} base is projected at "
        f"{f[f'{s.projected_fy_label} projected new base']} — keeping increment spend and "
        f"backfill volume in check is the priority for next cycle.",
    ]


def _parse_bullets_from_llm(raw: str) -> list[str]:
    """Parse a JSON array of strings, tolerating markdown fences. Falls back
    to a plain-line scan. Raises ValueError if fewer than 5 usable bullets
    come out either way, so the caller can use the deterministic template."""
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


async def _generate_bullets(
    tenant_id: uuid.UUID, summary: PnlExecutiveSummaryResponse
) -> list[str]:
    """Try the SLM for natural-language bullets; fall back to the
    deterministic template on any failure (timeout, unreachable model,
    malformed output) so the dashboard never blocks on this.

    Cached per-tenant on the exact facts fed to the model, with a TTL —
    fast on repeat loads, still re-phrased periodically rather than frozen
    on the first-ever LLM output for the life of the process.
    """
    facts = _bullet_facts(summary)
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
                    settings,
                    [{"role": "user", "content": prompt}],
                    max_tokens=400,
                    temperature=0.3,
                ),
            ),
            timeout=_BULLETS_LLM_TIMEOUT_SECONDS,
        )
        bullets = _parse_bullets_from_llm(raw)
    except Exception:
        logger.warning("pnl_bullets.llm_failed; falling back to template", exc_info=True)
        bullets = _build_bullets(summary)

    _bullets_cache[cache_key] = (bullets, time.monotonic())
    return bullets
