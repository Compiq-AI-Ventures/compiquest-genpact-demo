"""seed_demo_stages.py — Fix accuracy + set compelling demo workflow stages.

Data accuracy fixes (all users):
  1. Align market_position with compa_ratio
  2. Fix recommended_level for READY users (next level up)
  3. Risk callout text: only CRITICAL users, realistic messages
  4. Clean inconsistent draft recs for MoM4

Demo stages after seeding:
  MoM1 (Otto)     → Full cycle: MoPs submitted budgets + all IC recs + Otto APPROVED
  MoM2 (Curt)     → Pay recs in progress (mix per MoP)
  MoM3 (Quentin)  → Budget allocated to MoPs (no recs yet)
  MoM4 (Adrian)   → Budget planning (has budget pool, building allocation)

Run: uv run python3 scripts/seed_demo_stages.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import AsyncSessionLocal
from app.models.budget_allocation import (
    BudgetAllocation,
    BudgetAllocationLine,
    BudgetAllocationStatus,
)
from app.models.compensation_cycle import CompensationCycle
from app.models.jvre_snapshot import JvreSnapshot
from app.models.market_benchmark import MarketBenchmark
from app.models.pay_recommendation import PayRecommendation, PayRecommendationComponent
from app.models.reporting_relationship import ReportingRelationship
from app.models.user import User
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

# ── Constants ──────────────────────────────────────────────────────────────
# TENANT_ID is resolved at the start of main() by looking up the demo tenant
# by its stable ``code``. The placeholder None lets module-level helpers
# import cleanly; main() reassigns it via ``global`` before any helper runs.
# See seed_departments.py / seed_job_titles.py for the same pattern.
TENANT_CODE = "oscorp"
TENANT_ID: uuid.UUID | None = None
CURRENCY = "USD"

T_BUDGET_SUBMIT = datetime(2026, 3, 5, tzinfo=UTC)
T_MOP_ALLOC = datetime(2026, 3, 10, tzinfo=UTC)
T_PAY_REC_DRAFT = datetime(2026, 3, 20, tzinfo=UTC)
T_PAY_REC_SUBMIT = datetime(2026, 4, 10, tzinfo=UTC)
T_MOM_APPROVE = datetime(2026, 4, 25, tzinfo=UTC)

PAY_COMPONENTS = [
    "BASE_PAY",
    "VARIABLE_PAY",
    "LTI_GRANT_FMV",
    "OTHER_REWARDS",
    "LTI_UNITS",
]

LEVEL_PROGRESSION = {
    "L1": "L2",
    "L2": "L3",
    "L3": "L4",
    "L4": "L5",
    "L5": "L5",
    "M1": "M2",
    "M2": "M2",
}

RISK_TEXTS = [
    "External offer from competitor detected. Retention bonus recommended.",
    "Below P25 for level. High flight risk — compensation review urgent.",
    "Post-vest cliff approaching in Q2. No equity refresh scheduled.",
    "High performer with 4/5 rating. Active on LinkedIn and in job market.",
    "3 competing offers in last 6 months. Critical project dependency.",
    "Flagged by manager as at-risk. Peer-level comp gap of 15% identified.",
    "Recruiter contact confirmed. Key owner of critical infrastructure work.",
    "Performance-pay mismatch detected. Promotion overdue by one cycle.",
]


# ── Helpers ────────────────────────────────────────────────────────────────
def round_money(v: Decimal) -> Decimal:
    return (v / Decimal("100")).quantize(Decimal("1")) * Decimal("100")


def market_pos_from_cr(cr: Decimal) -> str:
    if cr >= Decimal("1.05"):
        return "ABOVE_MARKET"
    elif cr >= Decimal("0.95"):
        return "MARKET_ALIGNED"
    return "BELOW_MARKET"


async def get_user(db: AsyncSession, email: str) -> User:
    u = (
        await db.execute(select(User).where(User.email == email, User.tenant_id == TENANT_ID))
    ).scalar_one_or_none()
    if not u:
        raise ValueError(f"User not found: {email}")
    return u


async def get_alloc(db: AsyncSession, owner_id: uuid.UUID) -> BudgetAllocation | None:
    return (
        await db.execute(
            select(BudgetAllocation).where(
                BudgetAllocation.owner_user_id == owner_id,
                BudgetAllocation.tenant_id == TENANT_ID,
            )
        )
    ).scalar_one_or_none()


async def get_snap(db: AsyncSession, uid: uuid.UUID, cycle_id: uuid.UUID) -> JvreSnapshot | None:
    return (
        await db.execute(
            select(JvreSnapshot).where(
                JvreSnapshot.subject_user_id == uid,
                JvreSnapshot.cycle_id == cycle_id,
                JvreSnapshot.tenant_id == TENANT_ID,
            )
        )
    ).scalar_one_or_none()


async def get_reports(db: AsyncSession, mgr_id: uuid.UUID, cycle_id: uuid.UUID) -> list[uuid.UUID]:
    rows = (
        await db.execute(
            select(ReportingRelationship.report_user_id).where(
                ReportingRelationship.manager_user_id == mgr_id,
                ReportingRelationship.cycle_id == cycle_id,
                ReportingRelationship.tenant_id == TENANT_ID,
            )
        )
    ).fetchall()
    return [r[0] for r in rows]


async def jvre_pool(db: AsyncSession, root_id: uuid.UUID, cycle_id: uuid.UUID) -> Decimal:
    """Sum JVRE recs for root + all direct reports (IC subtree)."""
    ids = [root_id] + await get_reports(db, root_id, cycle_id)
    total = Decimal("0")
    for uid in ids:
        s = await get_snap(db, uid, cycle_id)
        if s:
            total += s.recommended_base or Decimal("0")
            total += s.recommended_variable or Decimal("0")
            total += s.recommended_lti_fmv or Decimal("0")
            total += s.recommended_other_rewards or Decimal("0")
    return round_money(total)


async def upsert_rec(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID,
    subject_id: uuid.UUID,
    cycle_id: uuid.UUID,
    rel_kind: str,
    status: str,
    submitted_at: datetime | None = None,
    approved_at: datetime | None = None,
    mom_vals: dict[str, Decimal] | None = None,
) -> PayRecommendation:
    existing = (
        await db.execute(
            select(PayRecommendation).where(
                PayRecommendation.actor_user_id == actor_id,
                PayRecommendation.subject_user_id == subject_id,
                PayRecommendation.cycle_id == cycle_id,
                PayRecommendation.tenant_id == TENANT_ID,
            )
        )
    ).scalar_one_or_none()

    if existing:
        existing.status = status
        if submitted_at:
            existing.submitted_at = submitted_at
            if not existing.saved_at:
                existing.saved_at = submitted_at
        if approved_at:
            existing.approved_at = approved_at
        # Update mom_rec_values if provided
        if mom_vals:
            comps = (
                (
                    await db.execute(
                        select(PayRecommendationComponent).where(
                            PayRecommendationComponent.recommendation_id == existing.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            for comp in comps:
                if comp.component in mom_vals:
                    comp.mom_rec_value = mom_vals[comp.component]
        await db.flush()
        return existing

    rec = PayRecommendation(
        tenant_id=TENANT_ID,
        cycle_id=cycle_id,
        actor_user_id=actor_id,
        subject_user_id=subject_id,
        relationship_kind=rel_kind,
        status=status,
        currency_code=CURRENCY,
        submitted_at=submitted_at,
        approved_at=approved_at,
        saved_at=submitted_at or T_PAY_REC_DRAFT,
    )
    db.add(rec)
    await db.flush()

    snap = await get_snap(db, subject_id, cycle_id)
    jvre: dict[str, Decimal] = {}
    if snap:
        jvre = {
            "BASE_PAY": snap.recommended_base or Decimal("0"),
            "VARIABLE_PAY": snap.recommended_variable or Decimal("0"),
            "LTI_GRANT_FMV": snap.recommended_lti_fmv or Decimal("0"),
            "OTHER_REWARDS": snap.recommended_other_rewards or Decimal("0"),
            "LTI_UNITS": Decimal(str(snap.recommended_lti_units or 0)),
        }

    for comp_name in PAY_COMPONENTS:
        val = jvre.get(comp_name, Decimal("0"))
        db.add(
            PayRecommendationComponent(
                recommendation_id=rec.id,
                component=comp_name,
                current_value=None,
                jvre_rec_value=val,
                mgr_rec_value=val,
                mom_rec_value=mom_vals.get(comp_name) if mom_vals else None,
                currency_code=CURRENCY,
            )
        )
    await db.flush()
    return rec


async def submit_alloc(
    db: AsyncSession,
    alloc: BudgetAllocation,
    by: uuid.UUID,
    at: datetime,
) -> bool:
    if alloc.status != BudgetAllocationStatus.SUBMITTED.value:
        alloc.status = BudgetAllocationStatus.SUBMITTED.value
        alloc.submitted_at = at
        alloc.submitted_by_user_id = by
        await db.flush()
        return True
    return False


async def ensure_mop_alloc(
    db: AsyncSession,
    *,
    cycle_id: uuid.UUID,
    owner_id: uuid.UUID,
    parent_id: uuid.UUID,
    pool: Decimal,
) -> tuple[BudgetAllocation, bool]:
    existing = await get_alloc(db, owner_id)
    if existing:
        return existing, False
    alloc = BudgetAllocation(
        tenant_id=TENANT_ID,
        cycle_id=cycle_id,
        owner_user_id=owner_id,
        parent_allocation_id=parent_id,
        total_pool=pool,
        strategic_reserve=Decimal("0"),
        budget_for_allocation=pool,
        currency_code=CURRENCY,
        status=BudgetAllocationStatus.PENDING.value,
    )
    db.add(alloc)
    await db.flush()
    return alloc, True


async def ensure_mom_lines(
    db: AsyncSession,
    alloc: BudgetAllocation,
    mop_ids: list[uuid.UUID],
    cycle_id: uuid.UUID,
) -> int:
    existing = (
        (
            await db.execute(
                select(BudgetAllocationLine).where(BudgetAllocationLine.allocation_id == alloc.id)
            )
        )
        .scalars()
        .all()
    )
    existing_ids = {line.recipient_user_id for line in existing}

    created = 0
    for mop_id in mop_ids:
        if mop_id in existing_ids:
            continue
        pool = round_money(await jvre_pool(db, mop_id, cycle_id) * Decimal("1.10"))
        jvre_rec = round_money(pool / Decimal("1.05"))
        db.add(
            BudgetAllocationLine(
                allocation_id=alloc.id,
                recipient_user_id=mop_id,
                allocated_amount=pool,
                base_pool=round_money(pool * Decimal("0.65")),
                variable_pool=round_money(pool * Decimal("0.20")),
                lti_grant_fmv_pool=round_money(pool * Decimal("0.10")),
                reserve_pool=round_money(pool * Decimal("0.05")),
                jvre_rec_amount=jvre_rec,
                currency_code=CURRENCY,
            )
        )
        created += 1
    if created:
        await db.flush()
    return created


# ── Step 1: Data accuracy fixes ────────────────────────────────────────────
async def fix_accuracy(db: AsyncSession, cycle_id: uuid.UUID) -> None:
    print("  Fixing market_position alignment with compa_ratio...")
    benchmarks = (
        (await db.execute(select(MarketBenchmark).where(MarketBenchmark.tenant_id == TENANT_ID)))
        .scalars()
        .all()
    )
    mkt_fixed = 0
    for bm in benchmarks:
        correct = market_pos_from_cr(bm.compa_ratio)
        snap = await get_snap(db, bm.subject_user_id, cycle_id)
        if snap and snap.market_position != correct:
            snap.market_position = correct
            mkt_fixed += 1
    print(f"    ✓ {mkt_fixed} market_position fields corrected")

    print("  Fixing recommended_level for READY users...")
    ready_snaps = (
        (
            await db.execute(
                select(JvreSnapshot).where(
                    JvreSnapshot.cycle_id == cycle_id,
                    JvreSnapshot.tenant_id == TENANT_ID,
                    JvreSnapshot.promotion_readiness == "READY",
                )
            )
        )
        .scalars()
        .all()
    )
    lvl_fixed = 0
    for snap in ready_snaps:
        curr = snap.recommended_level or "L1"
        next_lvl = LEVEL_PROGRESSION.get(curr, curr)
        if next_lvl != curr:
            snap.recommended_level = next_lvl
            lvl_fixed += 1
    print(f"    ✓ {lvl_fixed} recommended_level fields updated to next level")

    print("  Fixing risk_callout_text (CRITICAL only)...")
    all_snaps = (
        (
            await db.execute(
                select(JvreSnapshot).where(
                    JvreSnapshot.cycle_id == cycle_id,
                    JvreSnapshot.tenant_id == TENANT_ID,
                )
            )
        )
        .scalars()
        .all()
    )
    risk_fixed = 0
    critical_idx = 0
    for snap in all_snaps:
        if snap.criticality == "CRITICAL":
            if not snap.risk_callout_text:
                snap.risk_callout_text = RISK_TEXTS[critical_idx % len(RISK_TEXTS)]
                risk_fixed += 1
            critical_idx += 1
        elif snap.risk_callout_text:
            snap.risk_callout_text = None
            risk_fixed += 1
    print(f"    ✓ {risk_fixed} risk_callout_text fields updated")

    await db.flush()


# ── Step 2: Clean inconsistent data ───────────────────────────────────────
async def clean_inconsistent_data(db: AsyncSession, cycle_id: uuid.UUID) -> None:
    print("  Cleaning MoM4 draft recs (shouldn't exist at Budget Planning)...")
    mom4 = await get_user(db, "mom4@oscorp.example.com")
    deleted = 0
    recs = (
        (
            await db.execute(
                select(PayRecommendation).where(
                    PayRecommendation.actor_user_id == mom4.id,
                    PayRecommendation.cycle_id == cycle_id,
                    PayRecommendation.tenant_id == TENANT_ID,
                )
            )
        )
        .scalars()
        .all()
    )
    for rec in recs:
        # Delete components first
        await db.execute(
            delete(PayRecommendationComponent).where(
                PayRecommendationComponent.recommendation_id == rec.id
            )
        )
        await db.delete(rec)
        deleted += 1
    if deleted:
        await db.flush()
    print(f"    ✓ Deleted {deleted} inconsistent recs for MoM4")


# ── Stage setups ───────────────────────────────────────────────────────────
async def setup_mom4_budget_planning(db: AsyncSession, cycle: CompensationCycle) -> None:
    """Adrian — Budget Planning: has pool + lines, hasn't submitted."""
    adrian = await get_user(db, "mom4@oscorp.example.com")
    alloc = await get_alloc(db, adrian.id)
    if not alloc:
        print("  ⚠ MoM4 allocation not found")
        return

    mop_ids = [(await get_user(db, f"mop4-{i}@oscorp.example.com")).id for i in range(1, 5)]
    created = await ensure_mom_lines(db, alloc, mop_ids, cycle.id)
    print(f"  ✓ MoM4 stays PENDING — {created} allocation lines created, 0 MoP allocations")


async def setup_mom3_budget_allocated(db: AsyncSession, cycle: CompensationCycle) -> None:
    """Quentin — Budget Allocated: submitted to MoPs, MoPs have PENDING allocations, no recs."""
    quentin = await get_user(db, "mom3@oscorp.example.com")
    alloc = await get_alloc(db, quentin.id)
    if not alloc:
        print("  ⚠ MoM3 allocation not found")
        return

    mop_users = [await get_user(db, f"mop3-{i}@oscorp.example.com") for i in range(1, 5)]
    mop_ids = [u.id for u in mop_users]

    # Create lines for Quentin's allocation
    created_lines = await ensure_mom_lines(db, alloc, mop_ids, cycle.id)
    print(f"  ✓ {created_lines} allocation lines created for MoM3")

    # Submit Quentin's allocation
    submitted = await submit_alloc(db, alloc, quentin.id, T_BUDGET_SUBMIT)
    print(f"  ✓ MoM3 allocation {'SUBMITTED' if submitted else 'already SUBMITTED'}")

    # Create PENDING allocations for MoP3
    new_allocs = 0
    for mop in mop_users:
        pool = round_money(await jvre_pool(db, mop.id, cycle.id) * Decimal("1.10"))
        _, is_new = await ensure_mop_alloc(
            db,
            cycle_id=cycle.id,
            owner_id=mop.id,
            parent_id=alloc.id,
            pool=pool,
        )
        if is_new:
            new_allocs += 1
    print(
        f"  ✓ {new_allocs} PENDING allocations created for MoP3 ({len(mop_users) - new_allocs} already existed)"
    )


async def setup_mom2_pay_recs(db: AsyncSession, cycle: CompensationCycle) -> None:
    """Curt — Pay recs in progress.
    mop2-1: all 4 ICs SUBMITTED (done)
    mop2-2: first 3 of 5 SUBMITTED, rest DRAFT (in progress)
    mop2-3: all 6 DRAFT (just started)
    mop2-4: 0 recs (Budget Received — hasn't started)
    """
    stages = [
        ("mop2-1@oscorp.example.com", "ALL_SUBMITTED"),
        ("mop2-2@oscorp.example.com", "PARTIAL"),
        ("mop2-3@oscorp.example.com", "ALL_DRAFT"),
        ("mop2-4@oscorp.example.com", "NONE"),
    ]
    for email, mode in stages:
        mop = await get_user(db, email)
        ic_ids = await get_reports(db, mop.id, cycle.id)

        if mode == "NONE":
            print(f"  ↷ {email}: 0 recs — Budget Received stage")
            continue

        for i, ic_id in enumerate(ic_ids):
            if mode == "ALL_SUBMITTED":
                s, t = "SUBMITTED", T_PAY_REC_SUBMIT
            elif mode == "PARTIAL":
                # First 3 submitted, rest draft
                if i < 3:
                    s, t = "SUBMITTED", T_PAY_REC_SUBMIT
                else:
                    s, t = "DRAFT", None
            else:  # ALL_DRAFT
                s, t = "DRAFT", None

            await upsert_rec(
                db,
                actor_id=mop.id,
                subject_id=ic_id,
                cycle_id=cycle.id,
                rel_kind="MGR_FOR_IC",
                status=s,
                submitted_at=t,
            )
        sub = len(
            [
                x
                for x in range(len(ic_ids))
                if (mode == "ALL_SUBMITTED" or (mode == "PARTIAL" and x < 3))
            ]
        )
        print(
            f"  ✓ {email}: {len(ic_ids)} ICs — {sub} SUBMITTED, {len(ic_ids) - sub} DRAFT/NONE ({mode})"
        )


async def setup_mom1_full_cycle(db: AsyncSession, cycle: CompensationCycle) -> None:
    """Otto — Full cycle.
    All 4 MoP1s: allocation SUBMITTED + all IC recs SUBMITTED
    Otto: APPROVED rec for each MoP
    """
    otto = await get_user(db, "mom1@oscorp.example.com")
    mop_emails = [f"mop1-{i}@oscorp.example.com" for i in range(1, 5)]

    total_ic_recs = 0
    total_approved = 0

    for email in mop_emails:
        mop = await get_user(db, email)
        mop_alloc = await get_alloc(db, mop.id)

        # Submit MoP allocation
        if mop_alloc:
            await submit_alloc(db, mop_alloc, mop.id, T_MOP_ALLOC)

        # Get all IC direct reports
        ic_ids = await get_reports(db, mop.id, cycle.id)

        # All IC recs → SUBMITTED
        for ic_id in ic_ids:
            await upsert_rec(
                db,
                actor_id=mop.id,
                subject_id=ic_id,
                cycle_id=cycle.id,
                rel_kind="MGR_FOR_IC",
                status="SUBMITTED",
                submitted_at=T_PAY_REC_SUBMIT,
            )
            total_ic_recs += 1

        # Otto approves MoP with slight adjustment on base pay
        snap = await get_snap(db, mop.id, cycle.id)
        mom_vals = None
        if snap:
            mom_vals = {
                "BASE_PAY": round_money((snap.recommended_base or Decimal("0")) * Decimal("1.02")),
                "VARIABLE_PAY": snap.recommended_variable or Decimal("0"),
                "LTI_GRANT_FMV": snap.recommended_lti_fmv or Decimal("0"),
                "OTHER_REWARDS": snap.recommended_other_rewards or Decimal("0"),
                "LTI_UNITS": Decimal(str(snap.recommended_lti_units or 0)),
            }

        await upsert_rec(
            db,
            actor_id=otto.id,
            subject_id=mop.id,
            cycle_id=cycle.id,
            rel_kind="MOM_FOR_MGR",
            status="APPROVED",
            submitted_at=T_MOM_APPROVE,
            approved_at=T_MOM_APPROVE,
            mom_vals=mom_vals,
        )
        total_approved += 1
        alloc_status = "SUBMITTED" if mop_alloc else "NO_ALLOC"
        print(f"  ✓ {email}: alloc={alloc_status}, {len(ic_ids)} IC recs SUBMITTED, Otto APPROVED")

    await db.flush()
    print(f"  → MoM1 complete: {total_ic_recs} IC recs + {total_approved} MoM approvals")


# ── Summary verification ───────────────────────────────────────────────────
async def print_summary(db: AsyncSession, cycle_id: uuid.UUID) -> None:
    r = (
        await db.execute(
            text("""
        SELECT
            u.email,
            u.first_name || chr(32) || u.last_name as name,
            ba.status as alloc_status,
            ba.total_pool,
            COUNT(DISTINCT rr.report_user_id) as reports,
            COUNT(DISTINCT pr.id) as recs,
            COUNT(DISTINCT CASE WHEN pr.status='SUBMITTED' THEN pr.id END) as sub,
            COUNT(DISTINCT CASE WHEN pr.status='DRAFT' THEN pr.id END) as draft,
            COUNT(DISTINCT CASE WHEN pr.status='APPROVED' THEN pr.id END) as appr
        FROM users u
        LEFT JOIN budget_allocations ba ON ba.owner_user_id=u.id AND ba.tenant_id=:tid
        LEFT JOIN reporting_relationships rr ON rr.manager_user_id=u.id AND rr.tenant_id=:tid AND rr.cycle_id=:cid
        LEFT JOIN pay_recommendations pr ON pr.actor_user_id=u.id AND pr.tenant_id=:tid AND pr.cycle_id=:cid
        WHERE u.tenant_id=:tid AND (u.email LIKE 'mom%' OR u.email LIKE 'mop%')
        GROUP BY u.email, u.first_name, u.last_name, ba.status, ba.total_pool
        ORDER BY u.email
    """),
            {"tid": str(TENANT_ID), "cid": str(cycle_id)},
        )
    ).fetchall()

    print(
        f"\n{'email':<35} {'alloc':<12} {'pool':>10} {'rpts':>5} {'recs':>5} {'sub':>4} {'draft':>5} {'appr':>5}"
    )
    print("-" * 90)
    for row in r:
        print(
            f"{row.email:<35} {row.alloc_status or 'NO_ALLOC':<12} "
            f"{int(row.total_pool) if row.total_pool else 0:>10,} "
            f"{row.reports:>5} {row.recs:>5} {row.sub:>4} {row.draft:>5} {row.appr:>5}"
        )


# ── Entry point ────────────────────────────────────────────────────────────
async def main() -> None:
    global TENANT_ID

    async with AsyncSessionLocal() as db:
        await db.execute(text("SET app.platform_override = 'true'"))

        # Resolve the demo tenant by code. Fails loudly if seed_demo_tenant
        # hasn't run yet — we'd rather error here than silently no-op against
        # a stale UUID.
        tenant_row = await db.execute(
            text("SELECT id FROM tenants WHERE code = :code"),
            {"code": TENANT_CODE},
        )
        resolved = tenant_row.scalar_one_or_none()
        if resolved is None:
            raise RuntimeError(
                f"Tenant code={TENANT_CODE!r} not found. "
                "Run `uv run python -m scripts.seed_demo_tenant` first."
            )
        TENANT_ID = uuid.UUID(str(resolved))

        cycle = (
            await db.execute(
                select(CompensationCycle).where(
                    CompensationCycle.tenant_id == TENANT_ID,
                    CompensationCycle.status == "ACTIVE",
                )
            )
        ).scalar_one_or_none()

        if not cycle:
            print("ERROR: No active cycle found")
            return

        print(f"Active cycle: {cycle.fy_label} ({cycle.id})\n")

        print("=== Step 1: Data Accuracy Fixes ===")
        await fix_accuracy(db, cycle.id)

        print("\n=== Step 2: Clean Inconsistent Data ===")
        await clean_inconsistent_data(db, cycle.id)

        print("\n=== Step 3: MoM4 (Adrian) — Budget Planning ===")
        await setup_mom4_budget_planning(db, cycle)

        print("\n=== Step 4: MoM3 (Quentin) — Budget Allocated ===")
        await setup_mom3_budget_allocated(db, cycle)

        print("\n=== Step 5: MoM2 (Curt) — Pay Recs In Progress ===")
        await setup_mom2_pay_recs(db, cycle)

        print("\n=== Step 6: MoM1 (Otto) — Full Cycle Complete ===")
        await setup_mom1_full_cycle(db, cycle)

        await db.commit()

        print("\n=== Final State ===")
        await print_summary(db, cycle.id)

        print("\n" + "=" * 60)
        print("✅ Done! Demo login summary:")
        print("=" * 60)
        print(f"  {'Email':<42} {'Role':<10} Stage")
        print(f"  {'-' * 42} {'-' * 10} {'-' * 30}")
        print(f"  {'mom1@oscorp.example.com':<42} {'MoM':<10} Full cycle complete")
        print(f"  {'mom2@oscorp.example.com':<42} {'MoM':<10} Pay recs in progress")
        print(f"  {'mom3@oscorp.example.com':<42} {'MoM':<10} Budget allocated to MoPs")
        print(f"  {'mom4@oscorp.example.com':<42} {'MoM':<10} Budget planning")
        print(f"  {'mop1-1@oscorp.example.com':<42} {'Manager':<10} Recs submitted (Eddie Brock)")
        print(f"  {'mop1-2@oscorp.example.com':<42} {'Manager':<10} Recs submitted (Felicia Hardy)")
        print(
            f"  {'mop2-1@oscorp.example.com':<42} {'Manager':<10} Recs submitted (Herman Schultz)"
        )
        print(f"  {'mop2-2@oscorp.example.com':<42} {'Manager':<10} Recs partial (Phineas Mason)")
        print(f"  {'mop2-3@oscorp.example.com':<42} {'Manager':<10} Recs draft (Roderick Kingsley)")
        print(f"  {'mop2-4@oscorp.example.com':<42} {'Manager':<10} Budget received (Morris Bench)")
        print("\n  Password (all users): oscorp-demo-12345")


if __name__ == "__main__":
    asyncio.run(main())
