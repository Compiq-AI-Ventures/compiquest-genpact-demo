"""Seed the ``genpact`` transactional / demo tenant from real workbook data.

This is the layer the pay-review UI actually drives. It is built from the
already-loaded ``genpact_employee_master`` analytics table (run
``scripts.seed_genpact_master_data`` first) plus the ``JVRE_output.xlsx``
recommendations:

* **Users** — every FY2026 employee (28,413); inactive/exited ones are
  flagged ``is_active = False``. Password is a single shared bcrypt hash.
* **Departments** — one per business unit (6).
* **Roles** — derived from management span: MANAGER_OF_MANAGERS (manages a
  manager), MANAGER (manages only ICs), IC (no reports). The root with the
  largest organisation is additionally granted CFO (budget owner); two more
  roots get CHRO / C&B for the exec-view demo.
* **Compensation cycles** — FY2023-FY2026; FY2026 is ACTIVE.
* **Reporting relationships** — the FY2026 org tree (manager → report).
* **Compensation history** — FY2023-FY2025 rows per subject.
* **Market benchmarks** — current vs target pay from external compa-ratio.
* **JVRE snapshots** — the JVRE_output recommendations for the active FY2026
  cohort (22,700). Fields absent from the sheet (criticality, market
  position, promotion readiness, JVRE score, risk / AI text) are derived
  from the employee's real signals (compa-ratio, performance, promotion
  flag) with a deterministic RNG keyed on the employee id.
* **JVRE rationale** — one generated narrative per snapshot.
* **Budget allocations** — the CFO root (SUBMITTED) + per-direct-report
  lines and PENDING child allocations, so the Budget Planner is demoable.

Pay recommendations are intentionally NOT seeded — they are created on the
first "Save & Next" in the workflow, exactly as in the live product.

Idempotent: clears the tenant's transactional rows, then rebuilds. The
``genpact_*`` analytics tables are left untouched.

Run::

    uv run python -m scripts.seed_genpact_master_data   # once, for master data
    uv run python -m scripts.seed_genpact_tenant
"""

from __future__ import annotations

import asyncio
import random
import re
import time
import uuid
import zlib
from datetime import date
from decimal import Decimal

import openpyxl
from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.models.budget_allocation import (
    BudgetAllocation,
    BudgetAllocationLine,
    BudgetAllocationStatus,
)
from app.models.compensation_cycle import CompensationCycle, CompensationCycleStatus
from app.models.compensation_history import CompensationHistory
from app.models.department import Department
from app.models.iquest_engine_output import IquestEngineOutput
from app.models.jvre_rationale import JvreRationale
from app.models.jvre_snapshot import (
    JvreCriticality,
    JvreMarketPosition,
    JvrePromotionReadiness,
    JvreSnapshot,
)
from app.models.market_benchmark import MarketBenchmark
from app.models.reporting_relationship import ReportingRelationship
from app.models.role import Role
from app.models.user import User
from app.models.user_role import UserRole
from sqlalchemy import delete, select, text

from scripts._genpact_common import (
    ACTIVE_CYCLE_FY,
    DEFAULT_CURRENCY,
    DEMO_PASSWORD,
    JVRE_WORKBOOK,
    get_or_create_tenant,
)

_BATCH = 5000
_CURRENCY_SYMBOL = {"INR": "₹", "USD": "$", "PLN": "zł", "PHP": "₱", "MXN": "$"}
# Approximate local-currency units per 1 USD (for equity USD-equivalent display).
_USD_RATE = {"INR": 83.0, "USD": 1.0, "PLN": 4.0, "PHP": 56.0, "MXN": 17.0}
# "As-of" date for the active cycle (matches the Budget Planner's shown date).
_SEED_DATE = date(2026, 3, 4)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _split_name(full: str) -> tuple[str, str]:
    parts = (full or "").strip().split()
    if not parts:
        return "Employee", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", ".", (s or "").lower()).strip(".")


def _dec(v, q="0.01") -> Decimal:
    try:
        return Decimal(str(v)).quantize(Decimal(q))
    except Exception:
        return Decimal("0").quantize(Decimal(q))


def _market_position(compa: float) -> str:
    if compa and compa < 0.90:
        return JvreMarketPosition.BELOW_MARKET.value
    if compa and compa > 1.10:
        return JvreMarketPosition.ABOVE_MARKET.value
    return JvreMarketPosition.MARKET_ALIGNED.value


def _promotion_readiness(promo_upcoming: bool, perf: float) -> str:
    if promo_upcoming:
        return JvrePromotionReadiness.READY.value
    if perf and perf >= 3.0:
        return JvrePromotionReadiness.CANDIDATE.value
    return JvrePromotionReadiness.NOT_READY.value


def _criticality(perf: float, market_pos: str, is_manager: bool) -> str:
    below = market_pos == JvreMarketPosition.BELOW_MARKET.value
    if perf and perf >= 4.0 and (below or is_manager):
        return JvreCriticality.CRITICAL.value
    if perf and perf >= 2.8:
        return JvreCriticality.MODERATE_HIGH.value
    return JvreCriticality.LOW_RISK.value


_JVRE_SCORE_RANGE = {
    (JvreCriticality.CRITICAL.value, JvreMarketPosition.BELOW_MARKET.value): (7.5, 9.8),
    (JvreCriticality.CRITICAL.value, JvreMarketPosition.MARKET_ALIGNED.value): (6.5, 8.5),
    (JvreCriticality.MODERATE_HIGH.value, JvreMarketPosition.MARKET_ALIGNED.value): (4.0, 7.0),
    (JvreCriticality.MODERATE_HIGH.value, JvreMarketPosition.BELOW_MARKET.value): (5.0, 7.5),
    (JvreCriticality.LOW_RISK.value, JvreMarketPosition.ABOVE_MARKET.value): (1.5, 4.5),
}


def _jvre_score(criticality: str, market_pos: str, promo: str, rng: random.Random) -> Decimal:
    lo, hi = _JVRE_SCORE_RANGE.get((criticality, market_pos), (3.0, 6.0))
    if promo == JvrePromotionReadiness.READY.value:
        lo = min(lo + 0.8, hi)
    return _dec(rng.uniform(lo, hi))


def _jvre_tier(score: Decimal) -> str:
    if score >= 7:
        return "HIGH"
    if score >= 4:
        return "MODERATE"
    return "LOW"


def _ai_text(market_pos: str, growth_pct: int, sym: str) -> str:
    if market_pos == JvreMarketPosition.BELOW_MARKET.value:
        return (
            f"Market deficit identified; recommended +{growth_pct}% to restore"
            " competitive parity and reduce near-term attrition risk."
        )
    if market_pos == JvreMarketPosition.ABOVE_MARKET.value:
        return (
            f"Above-market positioning maintained; +{growth_pct}% sustains the"
            " premium reflecting role scarcity and demonstrated impact."
        )
    return f"Recommended +{growth_pct}% to sustain market alignment through the next cycle."


def _rationale_text(
    name: str,
    title: str,
    cur_base: Decimal,
    rec_base: Decimal,
    criticality: str,
    market_pos: str,
    promo: str,
    score: Decimal,
    sym: str,
) -> str:
    growth = (
        int((((rec_base - cur_base) / cur_base) * 100).quantize(Decimal("1"))) if cur_base else 0
    )
    first = name.split()[0] if name else "This employee"
    market_map = {
        JvreMarketPosition.BELOW_MARKET.value: (
            "Currently below market benchmarks, this adjustment closes the pay gap "
            "and reduces attrition risk to competitors."
        ),
        JvreMarketPosition.MARKET_ALIGNED.value: (
            "This adjustment maintains market alignment as external benchmarks move."
        ),
        JvreMarketPosition.ABOVE_MARKET.value: (
            "Already above market, this moderate increase sustains a premium that "
            "reflects role scarcity and impact."
        ),
    }
    crit_map = {
        JvreCriticality.CRITICAL.value: (
            f"{first}'s role carries critical organisational weight — replacement "
            f"cost and disruption significantly exceed this investment."
        ),
        JvreCriticality.MODERATE_HIGH.value: (
            f"The role holds moderate-to-high strategic importance; sustaining "
            f"{first}'s engagement protects delivery capacity."
        ),
        JvreCriticality.LOW_RISK.value: (
            f"Replacement risk is lower here, but recognising {first}'s contribution "
            f"reinforces retention norms across the team."
        ),
    }
    promo_map = {
        JvrePromotionReadiness.READY.value: (
            f"{first} has demonstrated readiness for the next level; a promotion "
            f"should be actioned alongside this adjustment."
        ),
        JvrePromotionReadiness.CANDIDATE.value: (
            f"{first} is tracking toward promotion eligibility; this increase keeps "
            f"headroom for the next cycle."
        ),
        JvrePromotionReadiness.NOT_READY.value: (
            f"{first} is not up for promotion this cycle; the adjustment recognises "
            f"in-role performance and manages market drift."
        ),
    }
    return (
        f"A {growth}% base adjustment is recommended for {name}, {title}, bringing base "
        f"pay to {sym}{int(rec_base):,}. Informed by a JVRE score of {score}/10.\n\n"
        f"{market_map[market_pos]}\n\n{crit_map[criticality]}\n\n{promo_map[promo]}"
    )


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------
async def _wipe_transactional(db, tenant_id) -> None:
    """Delete the tenant's transactional rows (analytics tables untouched)."""
    from app.models.pay_recommendation import PayRecommendation

    for model in (
        BudgetAllocation,
        PayRecommendation,
        IquestEngineOutput,
        JvreRationale,
        JvreSnapshot,
        MarketBenchmark,
        CompensationHistory,
        ReportingRelationship,
    ):
        await db.execute(delete(model).where(model.tenant_id == tenant_id))
    # user_roles cascade from users; delete users, then cycles + departments.
    user_ids = (
        (await db.execute(select(User.id).where(User.tenant_id == tenant_id))).scalars().all()
    )
    if user_ids:
        await db.execute(delete(UserRole).where(UserRole.user_id.in_(user_ids)))
    await db.execute(delete(User).where(User.tenant_id == tenant_id))
    await db.execute(delete(CompensationCycle).where(CompensationCycle.tenant_id == tenant_id))
    await db.execute(delete(Department).where(Department.tenant_id == tenant_id))
    await db.flush()


async def _bulk(db, table, rows: list[dict]) -> None:
    for i in range(0, len(rows), _BATCH):
        await db.execute(table.insert(), rows[i : i + _BATCH])


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
async def main() -> None:
    t0 = time.perf_counter()

    async with AsyncSessionLocal() as db:
        tenant = await get_or_create_tenant(db)
        tid = tenant.id
        print(f"Tenant {tenant.code} ({tid}) — wiping old transactional rows…")
        await _wipe_transactional(db, tid)

        # role ids
        roles = {r.code: r.id for r in (await db.execute(select(Role))).scalars().all()}

        # --- FX (local currency -> USD) ------------------------------------
        # Everything in the transactional layer is stored in the tenant's
        # reporting currency (USD). Employees are paid in local currencies
        # (INR / PLN / USD / MXN / PHP); ``genpact_currency_master`` stores
        # the USD-relative rate for each local currency at each cycle year
        # (e.g. FY2026: 1 USD = 92 INR). Local -> USD is a straight divide.
        # We build the lookup once and reuse via ``_usd(amount, ccy)``.
        active_year = ACTIVE_CYCLE_FY.replace("FY", "")
        fx_rows = (
            await db.execute(
                text(
                    "SELECT local_currency, conversion_value FROM genpact_currency_master "
                    "WHERE tenant_id=:tid AND reporting_cycle=:yr AND reporting_currency='USD'"
                ),
                {"tid": str(tid), "yr": active_year},
            )
        ).mappings().all()
        local_per_usd: dict[str, float] = {r["local_currency"]: float(r["conversion_value"]) for r in fx_rows}
        local_per_usd.setdefault("USD", 1.0)

        def _usd(amount, ccy: str | None) -> Decimal:
            """Convert a local-currency amount to USD.

            Returns Decimal(0) for a null/non-numeric input. Unknown currency
            codes are treated as USD (rate 1.0) — safer than dropping the row.
            """
            if amount in (None, ""):
                return Decimal("0")
            try:
                val = float(amount)
            except (TypeError, ValueError):
                return Decimal("0")
            rate = local_per_usd.get(ccy or "USD", 1.0) or 1.0
            return Decimal(str(round(val / rate, 2)))

        print(f"  FX rates (FY{active_year}): {local_per_usd}")

        # --- read FY2026 employees from the analytics table ----------------
        print("Reading FY2026 employees from genpact_employee_master…")
        fy2026 = (
            (
                await db.execute(
                    text("""
            SELECT employee_id, employee_name, business_unit, department, job_family,
                   designation, job_level, manager_employee_id, status, currency,
                   base_salary_pre, base_salary_post, variable_post, external_compa_post,
                   performance_rating, promotion_flag_upcoming, post_promotion_level,
                   span_direct, span_indirect, gender, location_city, joining_date,
                   company_experience_years, lti_eligible, lti_grant_value,
                   lti_unvested_remaining, next_vesting_date, total_increment_pct
            FROM genpact_employee_master WHERE fiscal_year = '2026'
        """)
                )
            )
            .mappings()
            .all()
        )
        emp = {r["employee_id"]: dict(r) for r in fy2026}
        print(f"  {len(emp):,} FY2026 employees")

        # --- roles by management span --------------------------------------
        managers = {r["manager_employee_id"] for r in fy2026 if r["manager_employee_id"]}
        managers &= set(emp)  # only those present
        mom = {
            r["manager_employee_id"]
            for r in fy2026
            if r["manager_employee_id"] in managers and r["employee_id"] in managers
        }

        def role_for(eid: str) -> str:
            if eid in mom:
                return "MANAGER_OF_MANAGERS"
            if eid in managers:
                return "MANAGER"
            return "IC"

        # --- departments (one per BU) --------------------------------------
        bus = sorted({r["business_unit"] for r in fy2026 if r["business_unit"]})
        dept_id = {bu: uuid.uuid4() for bu in bus}
        await _bulk(
            db,
            Department.__table__,
            [
                {"id": dept_id[bu], "tenant_id": tid, "code": _slug(bu)[:64] or "bu", "name": bu}
                for bu in bus
            ],
        )
        print(f"  {len(bus)} departments")

        # --- users ---------------------------------------------------------
        shared_hash = hash_password(DEMO_PASSWORD)
        uid = {eid: uuid.uuid4() for eid in emp}
        seen_email: dict[str, int] = {}
        user_rows, role_rows = [], []
        for eid, r in emp.items():
            first, last = _split_name(r["employee_name"])
            base_slug = _slug(f"{first}.{last}") or eid.lower()
            if base_slug in seen_email:
                seen_email[base_slug] += 1
                email_local = f"{base_slug}.{eid[2:].lstrip('0') or eid[2:]}"
            else:
                seen_email[base_slug] = 0
                email_local = base_slug
            user_rows.append(
                {
                    "id": uid[eid],
                    "tenant_id": tid,
                    "email": f"{email_local}@genpact.com"[:255],
                    "password_hash": shared_hash,
                    "first_name": first[:100],
                    "last_name": (last or None),
                    "job_title": (r["designation"] or None),
                    "department_id": dept_id.get(r["business_unit"]),
                    "is_active": r["status"] == "ACTIVE",
                }
            )
            role_rows.append(
                {"id": uuid.uuid4(), "user_id": uid[eid], "role_id": roles[role_for(eid)]}
            )
        await _bulk(db, User.__table__, user_rows)
        await _bulk(db, UserRole.__table__, role_rows)
        print(f"  {len(user_rows):,} users + role grants")

        # --- designate CFO / CHRO / C&B among the roots --------------------
        roots = sorted(
            (
                r
                for r in fy2026
                if r["status"] == "ACTIVE"
                and (not r["manager_employee_id"] or r["manager_employee_id"] not in emp)
            ),
            key=lambda r: r["span_indirect"] or 0,
            reverse=True,
        )
        exec_grants = []
        cfo_eid = None
        for code, r in zip(("CFO", "CHRO", "C_AND_B"), roots, strict=False):
            if code == "CFO":
                cfo_eid = r["employee_id"]
            exec_grants.append(
                {"id": uuid.uuid4(), "user_id": uid[r["employee_id"]], "role_id": roles[code]}
            )
        if exec_grants:
            await _bulk(db, UserRole.__table__, exec_grants)
        print(f"  exec grants: CFO={cfo_eid} (+CHRO,C&B) from {len(roots)} roots")

        # --- P&L Head (BU Head): one per business unit -----------------
        # A P&L Head is a real business-unit leader, not a title we invent:
        # among each BU's most-senior tier (job_level '1'), we grant the
        # role to whoever owns the LARGEST actual reporting subtree in that
        # BU — i.e. the person whose org actually is the business unit,
        # not just the first level-1 row alphabetically. This is an
        # additional role (kept alongside their base MANAGER_OF_MANAGERS
        # grant from role_for()), mirroring how CFO/CHRO/C&B are layered
        # on top of the base role.
        directs_of_active: dict[str, list[str]] = {}
        for r in fy2026:
            m = r["manager_employee_id"]
            if m in emp and r["status"] == "ACTIVE":
                directs_of_active.setdefault(m, []).append(r["employee_id"])

        def _subtree_size(root_eid: str) -> int:
            seen: set[str] = set()
            stack = list(directs_of_active.get(root_eid, []))
            while stack:
                node = stack.pop()
                if node in seen:
                    continue
                seen.add(node)
                stack.extend(directs_of_active.get(node, []))
            return len(seen)

        level1_by_bu: dict[str, list[str]] = {}
        for r in fy2026:
            if r["status"] == "ACTIVE" and r["job_level"] == "1":
                level1_by_bu.setdefault(r["business_unit"], []).append(r["employee_id"])

        # Real BU heads deliberately get NO role_rows change here — their
        # original login (e.g. sarah.smith.33395@genpact.com) stays exactly
        # what role_for() already gave it (MANAGER_OF_MANAGERS) and nothing
        # else, so it keeps showing ONLY the MoM experience end to end.
        pnl_heads: dict[str, str] = {}
        for bu, candidates in level1_by_bu.items():
            best_eid = max(candidates, key=_subtree_size, default=None)
            if best_eid is None or _subtree_size(best_eid) == 0:
                continue  # no one in this BU actually heads a real org
            pnl_heads[bu] = best_eid
        print(f"  P&L Head identified per BU: {dict(list(pnl_heads.items())[:6])}")

        # --- Dedicated P&L Head demo logins (PNL_HEAD + MANAGER_OF_MANAGERS) --
        # One login per BU, separate from the real head's own account, that
        # holds BOTH roles — this is the account that exercises the combined
        # PnL + MoM experience (Dashboard -> PnL, "Pay Recommendation
        # Dashboard" -> MoM, plus the usual MoM nav sections). Identity
        # (name, title, department) is copied from the real BU head row, so
        # the account still represents real org data — it's a second login
        # for the same leadership seat, not a fabricated person.
        pnl_demo_rows, pnl_demo_roles = [], []
        demo_uid_by_head_eid: dict[str, uuid.UUID] = {}
        for bu, head_eid in pnl_heads.items():
            head = emp[head_eid]
            first, last = _split_name(head["employee_name"])
            demo_uid = uuid.uuid4()
            demo_uid_by_head_eid[head_eid] = demo_uid
            slug = _slug(f"{first}.{last}") or head_eid.lower()
            pnl_demo_rows.append(
                {
                    "id": demo_uid,
                    "tenant_id": tid,
                    "email": f"{slug}.pnlhead@genpact.com"[:255],
                    "password_hash": shared_hash,
                    "first_name": first[:100],
                    "last_name": (last or None),
                    "job_title": f"P&L Head — {bu}",
                    "department_id": dept_id.get(bu),
                    "is_active": True,
                }
            )
            pnl_demo_roles.append(
                {"id": uuid.uuid4(), "user_id": demo_uid, "role_id": roles["PNL_HEAD"]}
            )
            pnl_demo_roles.append(
                {
                    "id": uuid.uuid4(),
                    "user_id": demo_uid,
                    "role_id": roles["MANAGER_OF_MANAGERS"],
                }
            )
        if pnl_demo_rows:
            await _bulk(db, User.__table__, pnl_demo_rows)
            await _bulk(db, UserRole.__table__, pnl_demo_roles)
        print(
            f"  P&L Head demo logins (PNL_HEAD + MANAGER_OF_MANAGERS): {len(pnl_demo_rows)} "
            f"({', '.join(r['email'] for r in pnl_demo_rows)})"
        )

        # --- compensation cycles -------------------------------------------
        cfo_uid = uid[cfo_eid]
        cycle_ids = {}
        cyc_rows = []
        for fy in ("FY2023", "FY2024", "FY2025", "FY2026"):
            cid = uuid.uuid4()
            cycle_ids[fy] = cid
            cyc_rows.append(
                {
                    "id": cid,
                    "tenant_id": tid,
                    "fy_label": fy,
                    "status": (
                        CompensationCycleStatus.ACTIVE.value
                        if fy == ACTIVE_CYCLE_FY
                        else CompensationCycleStatus.CLOSED.value
                    ),
                    "currency_code": DEFAULT_CURRENCY,
                    "created_by_user_id": cfo_uid,
                }
            )
        await _bulk(db, CompensationCycle.__table__, cyc_rows)
        active_cid = cycle_ids[ACTIVE_CYCLE_FY]
        print(f"  4 cycles (active {ACTIVE_CYCLE_FY})")

        # --- reporting relationships (active cycle) ------------------------
        # Only active employees are part of the live comp cycle. Exited
        # (inactive) employees stay in the genpact_* analytics tables for
        # attrition analysis but must not appear in a manager's review list
        # (they have no JVRE recommendation and would render as $0).
        rr_rows = [
            {
                "id": uuid.uuid4(),
                "tenant_id": tid,
                "cycle_id": active_cid,
                "manager_user_id": uid[r["manager_employee_id"]],
                "report_user_id": uid[r["employee_id"]],
            }
            for r in fy2026
            if r["manager_employee_id"] in emp and r["status"] == "ACTIVE"
        ]
        await _bulk(db, ReportingRelationship.__table__, rr_rows)
        print(f"  {len(rr_rows):,} reporting edges")
        # NOTE: reporting_relationships enforces one manager per (cycle,
        # report) — a report cannot have two managers in the same cycle —
        # so the P&L Head demo logins do NOT get mirrored reporting edges.
        # Their Team-Pay "my direct reports" list is legitimately empty;
        # their Budget Planner / Distribute-Budget screens are still fully
        # populated via the cloned BudgetAllocation + lines in _seed_budget
        # (those read budget_allocation_lines, not reporting_relationships).

        # --- compensation history (FY2023-2025) ----------------------------
        print("Reading prior-year rows for compensation history…")
        prior = (
            (
                await db.execute(
                    text("""
            SELECT employee_id, fiscal_year, job_level, currency,
                   base_salary_pre, base_salary_post, performance_rating_category,
                   promotion_flag
            FROM genpact_employee_master WHERE fiscal_year IN ('2023','2024','2025')
        """)
                )
            )
            .mappings()
            .all()
        )
        ch_rows = []
        for r in prior:
            u = uid.get(r["employee_id"])
            if u is None:
                continue
            change_local = int(r["base_salary_post"] or 0) - int(r["base_salary_pre"] or 0)
            ch_rows.append(
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tid,
                    "subject_user_id": u,
                    "fy_label": f"FY{r['fiscal_year']}",
                    "level_code": r["job_level"],
                    "comp_change_amount": _usd(change_local, r["currency"]),
                    "currency_code": DEFAULT_CURRENCY,
                    "perf_rating": (r["performance_rating_category"] or None),
                    "was_promoted": bool(r["promotion_flag"]),
                }
            )
        await _bulk(db, CompensationHistory.__table__, ch_rows)
        print(f"  {len(ch_rows):,} compensation-history rows")

        # --- market benchmarks -------------------------------------------------
        # ``current_pay`` MUST equal jvre_snapshots.current_base (i.e.
        # ``base_salary_pre`` = the JVRE workbook's "Base Prior Actual") so the
        # Base-Pay and Market-Benchmark cards on screen agree on the same
        # "Current" figure.
        # ``target_pay`` comes from the real market P50 in
        # ``genpact_benchmark`` matched by (BU, job_family, job_level,
        # currency, survey_year=ACTIVE), NOT from a pay/compa derivation. If
        # no benchmark row matches, we fall back to the P50 for the same
        # (job_level, currency) so every subject still gets a benchmark.
        print("Building benchmark P50 lookup from genpact_benchmark…")
        active_year = int(ACTIVE_CYCLE_FY.replace("FY", ""))
        bench_rows = (
            await db.execute(
                text("""
                    SELECT business_unit, job_family, job_level, currency,
                           MAX(base_p25) AS p25,
                           MAX(base_p50) AS p50,
                           MAX(base_p75) AS p75,
                           MAX(base_p90) AS p90
                    FROM genpact_benchmark
                    WHERE tenant_id = :tid AND survey_year = :yr
                    GROUP BY business_unit, job_family, job_level, currency
                """),
                {"tid": str(tid), "yr": active_year},
            )
        ).mappings().all()
        # Store full quartile tuple per key so the engine output can carry
        # P25/P50/P75 straight from the source benchmark.
        bench_by_key: dict[tuple[str, str, str, str], tuple[int, int, int, int]] = {}
        p50_by_key: dict[tuple[str, str, str, str], int] = {}
        p50_by_level_ccy: dict[tuple[str, str], list[int]] = {}
        for b in bench_rows:
            p50 = int(b["p50"] or 0)
            if not p50:
                continue
            k = (b["business_unit"], b["job_family"], b["job_level"], b["currency"])
            bench_by_key[k] = (
                int(b["p25"] or round(p50 * 0.85)),
                p50,
                int(b["p75"] or round(p50 * 1.15)),
                int(b["p90"] or round(p50 * 1.30)),
            )
            p50_by_key[k] = p50
            p50_by_level_ccy.setdefault((b["job_level"], b["currency"]), []).append(p50)
        # Fallback: median P50 per (level, currency).
        p50_fallback = {k: sorted(v)[len(v) // 2] for k, v in p50_by_level_ccy.items()}
        print(f"  {len(p50_by_key):,} exact benchmark keys + {len(p50_fallback):,} (level,ccy) fallbacks")

        mb_rows = []
        misses = 0
        for eid, r in emp.items():
            local_ccy = r["currency"] or "USD"
            # Current pay = prior-actual (matches JVRE current_base). Compa is
            # a ratio and stays the same regardless of currency, so we compute
            # it against the LOCAL P50, then convert both cur and target to
            # USD for storage.
            cur_local = int(r["base_salary_pre"] or 0)
            key = (r["business_unit"], r["job_family"], r["job_level"], local_ccy)
            p50_local = p50_by_key.get(key) or p50_fallback.get((r["job_level"], local_ccy)) or cur_local
            if key not in p50_by_key:
                misses += 1
            compa = (cur_local / p50_local) if p50_local else 1.0
            delta = int((cur_local - p50_local) / p50_local * 100) if p50_local else 0
            if delta < 0:
                dtxt = f"Under Target by {abs(delta)}%"
            elif delta > 0:
                dtxt = f"Above Target by {delta}%"
            else:
                dtxt = "Aligned with Target"
            mb_rows.append(
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tid,
                    "subject_user_id": uid[eid],
                    "current_pay": _usd(cur_local, local_ccy),
                    "target_pay": _usd(p50_local, local_ccy),
                    "currency_code": DEFAULT_CURRENCY,
                    "compa_ratio": _dec(compa, "0.0001"),
                    "target_compa_ratio_min": Decimal("0.95"),
                    "target_compa_ratio_max": Decimal("1.05"),
                    "delta_status_text": dtxt,
                }
            )
        await _bulk(db, MarketBenchmark.__table__, mb_rows)
        print(f"  {len(mb_rows):,} market benchmarks (fallback used for {misses:,} rows)")

        # --- JVRE snapshots + rationale (from JVRE_output.xlsx) ------------
        print(f"Reading {JVRE_WORKBOOK.name}…")
        wb = openpyxl.load_workbook(JVRE_WORKBOOK, read_only=True, data_only=True)
        ws = wb["JVRE_Output"]
        rows_iter = ws.iter_rows(values_only=True)
        header = [str(h).strip() for h in next(rows_iter)]
        col = {h: i for i, h in enumerate(header)}

        def g(row, name):
            return row[col[name]] if name in col else None

        snap_rows, rat_rows, eng_rows = [], [], []
        matched = skipped = 0
        for row in rows_iter:
            eid = str(g(row, "Employee ID")) if g(row, "Employee ID") else None
            r = emp.get(eid)
            if r is None:
                skipped += 1
                continue
            matched += 1
            # Raw local-currency figures from the JVRE workbook. We keep
            # local copies for computing % change / compa (which are
            # dimensionless), then convert every stored value to USD.
            local_ccy = r["currency"] or "USD"
            cur_base_local = _dec(g(row, "Base Prior Actual"), "1")
            cur_var_local = _dec(g(row, "Variable Prior Actual"), "1")
            rec_base_local = _dec(g(row, "Base New JVRE"), "1")
            rec_var_local = _dec(g(row, "Variable New JVRE"), "1")
            # USD-converted values (what actually gets stored).
            cur_base = _usd(cur_base_local, local_ccy)
            cur_var = _usd(cur_var_local, local_ccy)
            rec_base = _usd(rec_base_local, local_ccy)
            rec_var = _usd(rec_var_local, local_ccy)
            perf = float(g(row, "Performance Rating") or 0)
            compa = float(r["external_compa_post"] or 0) or 1.0
            is_mgr = eid in managers
            market_pos = _market_position(compa)
            promo = _promotion_readiness(bool(r["promotion_flag_upcoming"]), perf)
            crit = _criticality(perf, market_pos, is_mgr)
            r_rng = random.Random(zlib.crc32(eid.encode()))
            score = _jvre_score(crit, market_pos, promo, r_rng)
            cur_code = DEFAULT_CURRENCY  # stored currency; original was ``local_ccy``
            sym = "$"
            growth_pct = int((rec_base_local - cur_base_local) / cur_base_local * 100) if cur_base_local else 0
            rec_level = (
                r["post_promotion_level"]
                if (r["promotion_flag_upcoming"] and r["post_promotion_level"])
                else r["job_level"]
            )
            risk_text = None
            if crit == JvreCriticality.CRITICAL.value and is_mgr:
                team = r_rng.randint(4, 8)
                flagged = r_rng.randint(1, max(1, team // 3))
                risk_text = f"{flagged} of {team} reports flagged for retention risk; intervention recommended."
            sid = uuid.uuid4()
            snap_rows.append(
                {
                    "id": sid,
                    "tenant_id": tid,
                    "cycle_id": active_cid,
                    "subject_user_id": uid[eid],
                    "current_base": cur_base,
                    "current_variable": cur_var,
                    "jvre_score": score,
                    "recommended_base": rec_base,
                    "recommended_variable": rec_var,
                    "recommended_lti_fmv": None,
                    "recommended_lti_units": None,
                    "recommended_other_rewards": Decimal("0"),
                    "currency_code": cur_code,
                    "criticality": crit,
                    "market_position": market_pos,
                    "promotion_readiness": promo,
                    "recommended_level": rec_level,
                    "risk_callout_text": risk_text,
                    "ai_suggestion_text": _ai_text(market_pos, growth_pct, sym),
                }
            )
            rationale_val = _rationale_text(
                r["employee_name"],
                r["designation"] or role_for(eid),
                cur_base,
                rec_base,
                crit,
                market_pos,
                promo,
                score,
                sym,
            )
            rat_rows.append(
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tid,
                    "cycle_id": active_cid,
                    "subject_user_id": uid[eid],
                    "rationale_text": rationale_val,
                    "model_id": "seeded",
                }
            )

            # iquest_engine_output — the identity bridge (employee_id ↔
            # subject_user_id) + the recommendation record the iQuest AI tools
            # read. P25/P50/P75 come from the real ``genpact_benchmark`` for
            # the role, matching the Market-Benchmark card. Fallbacks keep
            # every row populated: (level, currency) median P50 with derived
            # quartiles, then a compa-derived P50 as last resort.
            # Benchmark quartiles come from ``genpact_benchmark`` in the
            # employee's LOCAL currency. Look up with the local key, then
            # convert to USD before writing to iquest_engine_output.
            _key = (r["business_unit"], r["job_family"], r["job_level"], local_ccy)
            _quartiles = bench_by_key.get(_key)
            if _quartiles:
                p25_local, p50_local, p75_local, _p90 = _quartiles
            else:
                p50_local = (
                    p50_fallback.get((r["job_level"], local_ccy))
                    or (round(float(cur_base_local) / compa) if compa else int(cur_base_local))
                )
                p25_local, p75_local = round(p50_local * 0.85), round(p50_local * 1.15)
            p25 = _usd(p25_local, local_ccy)
            p50 = _usd(p50_local, local_ccy)
            p75 = _usd(p75_local, local_ccy)

            # Equity / tenure / retention economics. ``unvested_usd`` is
            # already in USD by column contract; the LOCAL-currency
            # equity value we store on ``equity_value_inr`` (schema-legacy
            # column name, now really "equity_value_reporting_ccy") is the
            # USD-converted equivalent.
            unvested_local = int(r["lti_unvested_remaining"] or 0) if r["lti_eligible"] else 0
            unvested_usd_val = _usd(unvested_local, local_ccy) if unvested_local else None
            unvested_usd_int = int(unvested_usd_val) if unvested_usd_val is not None else None
            equity_val = unvested_usd_val
            nvd = r["next_vesting_date"]
            has_vest = bool(unvested_local) and nvd is not None and nvd.year > 1970
            next_vest = nvd if has_vest else None
            months_to_vest = (
                _dec(max((next_vest - _SEED_DATE).days, 0) / 30.0, "0.1") if next_vest else None
            )
            tenure_yrs = _dec(r["company_experience_years"], "0.1") if r["company_experience_years"] else None
            # Increased last cycle -> ~12 months ago; otherwise longer.
            increased = int(r["base_salary_post"] or 0) > int(r["base_salary_pre"] or 0)
            months_since_increase = 12 if increased else r_rng.randint(18, 30)
            cost_of_replacement = (cur_base * Decimal("1.8")).quantize(Decimal("1"))
            potential = _dec(min(5.0, max(1.0, perf)), "0.1") if perf else None

            eng_rows.append(
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tid,
                    "cycle_id": active_cid,
                    "subject_user_id": uid[eid],
                    "employee_id": eid,
                    "employee_name": r["employee_name"],
                    "department": r["department"],
                    "bu": r["business_unit"],
                    "job_family": r["job_family"],
                    "job_role": r["designation"],
                    "band": r["job_level"],
                    "designation": r["designation"],
                    "location": r["location_city"] or None,
                    "gender": r["gender"] or None,
                    "doj": r["joining_date"] if (r["joining_date"] and r["joining_date"].year > 1970) else None,
                    "tenure_years": tenure_yrs,
                    "potential_rating": potential,
                    "manager_criticality_score": _dec(min(Decimal("10"), score + 1), "0.1"),
                    "criticality_score": _dec(min(Decimal("10"), score + 1), "0.1"),
                    "cost_of_replacement_inr": cost_of_replacement,
                    "months_since_last_increase": months_since_increase,
                    "unvested_usd": unvested_usd_int,
                    "equity_value_inr": equity_val,
                    "next_vest_date": next_vest,
                    "months_to_next_vest": months_to_vest,
                    "rating_band": r["job_level"],
                    "perf_cycle": ACTIVE_CYCLE_FY,
                    "current_base_inr": cur_base,
                    "target_bonus_pct": _dec(float(cur_var) / float(cur_base), "0.0001")
                    if cur_base
                    else Decimal("0"),
                    "total_cash_inr": cur_base + cur_var,
                    "external_cr": _dec(compa, "0.0001"),
                    "benchmark_p25": Decimal(p25),
                    "benchmark_p50": Decimal(p50),
                    "benchmark_p75": Decimal(p75),
                    "jvre_score": score,
                    "jvre_tier": _jvre_tier(score),
                    "promotion_flag": bool(r["promotion_flag_upcoming"]),
                    "rec_new_base_inr": rec_base,
                    "rec_increase_pct": (
                        _dec((rec_base - cur_base) / cur_base, "0.0001")
                        if cur_base
                        else Decimal("0")
                    ),
                    "new_cr_after_rec": _dec(float(rec_base) / float(p50), "0.0001")
                    if p50
                    else Decimal("1"),
                    "rec_total_cash_inr": rec_base + rec_var,
                    "rationale": rationale_val,
                }
            )
        wb.close()
        await _bulk(db, JvreSnapshot.__table__, snap_rows)
        await _bulk(db, JvreRationale.__table__, rat_rows)
        await _bulk(db, IquestEngineOutput.__table__, eng_rows)
        print(f"  JVRE snapshots: {matched:,} matched, {skipped:,} skipped (not in FY2026)")
        print(f"  iquest_engine_output: {len(eng_rows):,} rows (AI bridge + recommendation)")

        # --- budget allocations for the CFO's org --------------------------
        await _seed_budget(
            db,
            tid=tid,
            active_cid=active_cid,
            cfo_eid=cfo_eid,
            uid=uid,
            emp=emp,
            fy2026=fy2026,
            mb_by_eid={r["employee_id"]: r for r in fy2026},
            demo_uid_by_head_eid=demo_uid_by_head_eid,
        )

        await db.commit()

    print(f"\nDone in {time.perf_counter() - t0:.1f}s")
    print("  Login domain : @genpact.com")
    print(f"  Password     : {DEMO_PASSWORD} (every user)")


async def _seed_budget(
    db, *, tid, active_cid, cfo_eid, uid, emp, fy2026, mb_by_eid, demo_uid_by_head_eid=None
) -> None:
    """One budget allocation per manager, wired into the reporting cascade.

    Every manager (anyone with ≥1 direct report) owns a BudgetAllocation for
    the active cycle. Rules that keep the numbers coherent:

    1. **Pool scope = full subtree.** Both ``total_pool`` and the runtime
       ``jvre_recommended_pool`` are computed over every descendant, so
       "JVRE Rec vs Pool" is an apples-to-apples comparison. Direct-reports-
       only would make JVRE Rec ~8x the pool for senior MoMs.
    2. **All money in USD.** Genpact's workforce is multi-currency but the
       tenant's reporting currency is USD; every subtree member's local pay
       is converted via ``genpact_currency_master`` FY2026 rates and the
       stamped ``currency_code`` on every allocation is USD.
    3. **Pool = subtree TCC (USD) × 1.10** (10 % growth envelope).
    4. **Line JVRE amount = subtree JVRE Rec (USD)** for that report's own
       subtree, so the cascade totals match at each level.

    The CFO's root is SUBMITTED; all others are PENDING.
    ``parent_allocation_id`` points at the parent manager's allocation so
    the cascade is structurally correct.
    """
    # --- FX rates for the active cycle: local → USD ------------------------
    active_year = ACTIVE_CYCLE_FY.replace("FY", "")
    fx_rows = (
        await db.execute(
            text(
                "SELECT local_currency, conversion_value FROM genpact_currency_master "
                "WHERE reporting_cycle=:yr AND reporting_currency='USD'"
            ),
            {"yr": active_year},
        )
    ).mappings().all()
    local_per_usd: dict[str, float] = {r["local_currency"]: float(r["conversion_value"]) for r in fx_rows}
    local_per_usd.setdefault("USD", 1.0)

    def _to_usd(local_amount: int, ccy: str) -> Decimal:
        rate = local_per_usd.get(ccy or "USD", 1.0) or 1.0
        return Decimal(str(round(float(local_amount) / rate, 2)))

    # --- Reporting graph: manager -> direct-report rows -------------------
    directs_of: dict[str, list] = {}
    for r in fy2026:
        m = r["manager_employee_id"]
        if m in emp and r["status"] == "ACTIVE":
            directs_of.setdefault(m, []).append(r)
    if not directs_of:
        print("  budget: no managers with reports — skipped")
        return

    # --- Subtree TCC (USD) per manager, memoised bottom-up ----------------
    # Each node's contribution is its own post-cycle TCC converted to USD
    # plus the sum of its children's already-computed subtree USD totals.
    def _own_tcc_usd(r: dict) -> Decimal:
        return _to_usd(
            int(r["base_salary_post"] or 0) + int(r["variable_post"] or 0),
            r["currency"] or "USD",
        )

    row_of = {r["employee_id"]: r for r in fy2026 if r["status"] == "ACTIVE"}
    subtree_tcc: dict[str, Decimal] = {}
    for eid in row_of:
        if eid in subtree_tcc:
            continue
        # Iterative DFS accumulating each node's TCC + children's subtrees.
        stack = [(eid, False)]
        while stack:
            node, processed = stack.pop()
            if processed:
                total = _own_tcc_usd(row_of[node])
                for child in directs_of.get(node, []):
                    total += subtree_tcc.get(child["employee_id"], Decimal("0"))
                subtree_tcc[node] = total
                continue
            if node in subtree_tcc:
                continue
            stack.append((node, True))
            for child in directs_of.get(node, []):
                if child["employee_id"] not in subtree_tcc:
                    stack.append((child["employee_id"], False))

    def budget_for_allocation_for(mgr_eid: str) -> Decimal:
        tot = sum(
            (subtree_tcc.get(r["employee_id"], Decimal("0")) for r in directs_of.get(mgr_eid, [])),
            Decimal("0"),
        )
        # 5% headroom above the direct reports' own (recommended-pay-based)
        # subtree totals, so a manager can always fund every report's full
        # JVRE recommendation without cutting anyone below it.
        return max((tot * Decimal("1.05")).quantize(Decimal("1")), Decimal("1000"))

    alloc_id_of = {mgr: uuid.uuid4() for mgr in directs_of}
    mgr_of = {r["employee_id"]: r["manager_employee_id"] for r in fy2026}

    _depth_cache: dict[str, int] = {}

    def depth(m: str) -> int:
        if m in _depth_cache:
            return _depth_cache[m]
        parent = mgr_of.get(m)
        d = depth(parent) + 1 if parent in alloc_id_of else 0
        _depth_cache[m] = d
        return d

    ordered_mgrs = sorted(directs_of, key=depth)

    alloc_rows, line_rows = [], []
    for mgr_eid in ordered_mgrs:
        directs = directs_of[mgr_eid]
        budget_for_allocation = budget_for_allocation_for(mgr_eid)
        reserve_pct = Decimal("0.05") if mgr_eid == cfo_eid else Decimal("0.115")
        # Reserve is additional headroom on top of budget_for_allocation, not
        # carved out of it — so it never eats into what reports are funded.
        reserve = (budget_for_allocation * reserve_pct).quantize(Decimal("1"))
        pool = budget_for_allocation + reserve
        aid = alloc_id_of[mgr_eid]
        parent_mgr = mgr_of.get(mgr_eid)
        alloc_rows.append(
            {
                "id": aid,
                "tenant_id": tid,
                "cycle_id": active_cid,
                "owner_user_id": uid[mgr_eid],
                "parent_allocation_id": alloc_id_of.get(parent_mgr),
                "total_pool": pool,
                "strategic_reserve": reserve,
                "budget_for_allocation": budget_for_allocation,
                "currency_code": DEFAULT_CURRENCY,
                "status": (
                    BudgetAllocationStatus.SUBMITTED.value
                    if mgr_eid == cfo_eid
                    else BudgetAllocationStatus.PENDING.value
                ),
                "submitted_by_user_id": (uid[cfo_eid] if mgr_eid == cfo_eid else None),
            }
        )
        # Line-level split: each direct report gets share proportional to
        # the size of THEIR OWN subtree (fair pro-rata cascade), and
        # ``jvre_rec_amount`` = the report's subtree TCC (INR) — the same
        # figure the runtime sums via _compute_jvre_pool_for. Since
        # budget_for_allocation = 1.05 * sum(subtree_totals), each line's
        # share works out to 1.05 * that report's own subtree total —
        # 5% above their JVRE recommendation, never below it.
        subtree_totals = [subtree_tcc.get(r["employee_id"], Decimal("0")) for r in directs]
        total_subtree = sum(subtree_totals, Decimal("0")) or Decimal("1")
        for r, sub in zip(directs, subtree_totals):
            share = (budget_for_allocation * (sub / total_subtree)).quantize(Decimal("1"))
            base = (share * Decimal("0.65")).quantize(Decimal("1"))
            var = (share * Decimal("0.20")).quantize(Decimal("1"))
            lti = (share * Decimal("0.10")).quantize(Decimal("1"))
            line_rows.append(
                {
                    "id": uuid.uuid4(),
                    "allocation_id": aid,
                    "recipient_user_id": uid[r["employee_id"]],
                    "allocated_amount": share,
                    "base_pool": base,
                    "variable_pool": var,
                    "lti_grant_fmv_pool": lti,
                    "reserve_pool": share - base - var - lti,
                    "jvre_rec_amount": sub.quantize(Decimal("1")),
                    "currency_code": DEFAULT_CURRENCY,
                }
            )
    await _bulk(db, BudgetAllocation.__table__, alloc_rows)
    await _bulk(db, BudgetAllocationLine.__table__, line_rows)
    print(
        f"  budget: {len(alloc_rows):,} manager allocations + {len(line_rows):,} lines "
        f"(subtree-based, USD-converted; CFO root SUBMITTED)"
    )

    # --- Clone each real BU head's own allocation + lines onto their
    # dedicated PNL_HEAD+MANAGER_OF_MANAGERS demo login. Additive only: the
    # real head's own row (inserted above, owner_user_id=uid[head_eid]) is
    # untouched, so that login's Budget Planner is unaffected. The clone
    # gives the demo login a real, populated Budget Planner/Team-Pay screen
    # instead of an empty one — same pool, same reserve, same per-report
    # lines, just a different owner and a fresh set of row ids. Inserted
    # AFTER the real rows above so ``parent_allocation_id`` (copied from the
    # real head's row, e.g. pointing at the CFO's allocation) always
    # references a row that already exists.
    demo_alloc_rows, demo_line_rows = [], []
    for head_eid, demo_uid in (demo_uid_by_head_eid or {}).items():
        head_aid = alloc_id_of.get(head_eid)
        if head_aid is None:
            continue
        head_alloc = next(a for a in alloc_rows if a["id"] == head_aid)
        demo_aid = uuid.uuid4()
        demo_alloc_rows.append({**head_alloc, "id": demo_aid, "owner_user_id": demo_uid})
        for line in line_rows:
            if line["allocation_id"] != head_aid:
                continue
            demo_line_rows.append({**line, "id": uuid.uuid4(), "allocation_id": demo_aid})
    if demo_alloc_rows:
        await _bulk(db, BudgetAllocation.__table__, demo_alloc_rows)
        await _bulk(db, BudgetAllocationLine.__table__, demo_line_rows)
        print(
            f"  budget (P&L Head demo logins): {len(demo_alloc_rows)} cloned allocations "
            f"+ {len(demo_line_rows)} cloned lines"
        )


if __name__ == "__main__":
    asyncio.run(main())
