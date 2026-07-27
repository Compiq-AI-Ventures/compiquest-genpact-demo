"""Shared utilities for JVRE-workspace e2e tests.

Builds a minimal org tree (CFO + 1 MoM + 1 MoP + 2 ICs) inside an
existing tenant + active cycle, with reporting relationships, JVRE
snapshots, and a CFO-funded MoM budget allocation. Designed for
:mod:`tests.test_jvre_workspace_e2e`.

Same shortcuts as :mod:`tests._helpers` (direct DB writes via the
session, single bcrypt hash shared across all users so the test is
fast). Production code never takes either shortcut.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.core.security import hash_password
from app.models.budget_allocation import (
    BudgetAllocation,
    BudgetAllocationLine,
    BudgetAllocationStatus,
)
from app.models.compensation_cycle import (
    CompensationCycle,
    CompensationCycleStatus,
)
from app.models.jvre_snapshot import (
    JvreCriticality,
    JvreMarketPosition,
    JvrePromotionReadiness,
    JvreSnapshot,
)
from app.models.reporting_relationship import ReportingRelationship
from app.models.role import Role
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user_role import UserRole
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

E2E_PASSWORD = "e2e-pass-12345"
E2E_TENANT_CODE = "e2etenant"
E2E_TENANT_DOMAIN = "e2etenant.example.com"


@dataclass(frozen=True)
class E2ETree:
    """Bag of seeded entities the e2e test asserts against."""

    tenant: Tenant
    cycle: CompensationCycle
    cfo: User
    mom: User
    mop: User
    ic1: User
    ic2: User
    cfo_alloc: BudgetAllocation
    mom_alloc: BudgetAllocation


async def _grant_role(
    db: AsyncSession, user: User, role_code: str
) -> None:
    role = (
        await db.execute(select(Role).where(Role.code == role_code))
    ).scalar_one()
    db.add(UserRole(user_id=user.id, role_id=role.id))


async def _make_user(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    email: str,
    password_hash: str,
    first_name: str,
    last_name: str,
) -> User:
    user = User(
        tenant_id=tenant_id,
        email=email,
        password_hash=password_hash,
        first_name=first_name,
        last_name=last_name,
    )
    db.add(user)
    await db.flush()
    return user


async def _add_jvre_snapshot(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    subject_user_id: uuid.UUID,
    base: Decimal,
    variable: Decimal,
    lti_fmv: Decimal,
    lti_units: int,
    other: Decimal = Decimal("0"),
    criticality: str = JvreCriticality.LOW_RISK.value,
    market_position: str = JvreMarketPosition.MARKET_ALIGNED.value,
    promotion_readiness: str = JvrePromotionReadiness.NOT_READY.value,
) -> JvreSnapshot:
    snap = JvreSnapshot(
        tenant_id=tenant_id,
        cycle_id=cycle_id,
        subject_user_id=subject_user_id,
        recommended_base=base,
        recommended_variable=variable,
        recommended_lti_fmv=lti_fmv,
        recommended_lti_units=lti_units,
        recommended_other_rewards=other,
        currency_code="USD",
        criticality=criticality,
        market_position=market_position,
        promotion_readiness=promotion_readiness,
        recommended_level=None,
        risk_callout_text=None,
        ai_suggestion_text=None,
    )
    db.add(snap)
    return snap


async def seed_minimal_jvre_tree(db: AsyncSession) -> E2ETree:
    """Build the minimum org for a full MoM → MoP → MoM-review walk.

    Org structure (4 users below the CFO):
      CFO  →  MoM  →  MoP  →  IC1
                          →  IC2

    Cycle is ACTIVE. CFO's allocation is SUBMITTED with one line
    (recipient = MoM); MoM's allocation is PENDING with no lines yet
    so the e2e test can drive Phase 4's align-with-jvre and submit
    fresh.

    Both ICs get JVRE snapshots with growth headroom built in so the
    MoM has plenty of budget to allocate JVRE-aligned without tripping
    Phase 4's overspend validation.
    """
    # Shared password hash — same trick as scripts/seed_loadtest.
    shared_hash = hash_password(E2E_PASSWORD)

    # Tenant + cycle.
    tenant = Tenant(
        code=E2E_TENANT_CODE,
        name="E2E Tenant",
        domain=E2E_TENANT_DOMAIN,
        status="ACTIVE",
        default_currency_code="USD",
    )
    db.add(tenant)
    await db.flush()

    cycle = CompensationCycle(
        tenant_id=tenant.id,
        fy_label="FY-E2E",
        status=CompensationCycleStatus.ACTIVE.value,
        currency_code="USD",
    )
    db.add(cycle)
    await db.flush()

    # Users.
    cfo = await _make_user(
        db,
        tenant_id=tenant.id,
        email=f"cfo@{E2E_TENANT_DOMAIN}",
        password_hash=shared_hash,
        first_name="E2E",
        last_name="CFO",
    )
    await _grant_role(db, cfo, "CFO")

    mom = await _make_user(
        db,
        tenant_id=tenant.id,
        email=f"mom@{E2E_TENANT_DOMAIN}",
        password_hash=shared_hash,
        first_name="E2E",
        last_name="MoM",
    )
    await _grant_role(db, mom, "MANAGER_OF_MANAGERS")

    mop = await _make_user(
        db,
        tenant_id=tenant.id,
        email=f"mop@{E2E_TENANT_DOMAIN}",
        password_hash=shared_hash,
        first_name="E2E",
        last_name="MoP",
    )
    await _grant_role(db, mop, "MANAGER")

    ic1 = await _make_user(
        db,
        tenant_id=tenant.id,
        email=f"ic1@{E2E_TENANT_DOMAIN}",
        password_hash=shared_hash,
        first_name="E2E",
        last_name="IC1",
    )
    ic2 = await _make_user(
        db,
        tenant_id=tenant.id,
        email=f"ic2@{E2E_TENANT_DOMAIN}",
        password_hash=shared_hash,
        first_name="E2E",
        last_name="IC2",
    )
    await db.flush()

    # Reporting relationships: MoM ← MoP ← IC1, IC2.
    db.add_all(
        [
            ReportingRelationship(
                tenant_id=tenant.id,
                cycle_id=cycle.id,
                manager_user_id=mom.id,
                report_user_id=mop.id,
            ),
            ReportingRelationship(
                tenant_id=tenant.id,
                cycle_id=cycle.id,
                manager_user_id=mop.id,
                report_user_id=ic1.id,
            ),
            ReportingRelationship(
                tenant_id=tenant.id,
                cycle_id=cycle.id,
                manager_user_id=mop.id,
                report_user_id=ic2.id,
            ),
        ]
    )
    await db.flush()

    # JVRE snapshots — round numbers for easy assertions.
    await _add_jvre_snapshot(
        db,
        tenant_id=tenant.id,
        cycle_id=cycle.id,
        subject_user_id=mop.id,
        base=Decimal("150000"),
        variable=Decimal("30000"),
        lti_fmv=Decimal("20000"),
        lti_units=20,
    )
    await _add_jvre_snapshot(
        db,
        tenant_id=tenant.id,
        cycle_id=cycle.id,
        subject_user_id=ic1.id,
        base=Decimal("80000"),
        variable=Decimal("16000"),
        lti_fmv=Decimal("8000"),
        lti_units=8,
        criticality=JvreCriticality.CRITICAL.value,
        market_position=JvreMarketPosition.BELOW_MARKET.value,
        promotion_readiness=JvrePromotionReadiness.READY.value,
    )
    await _add_jvre_snapshot(
        db,
        tenant_id=tenant.id,
        cycle_id=cycle.id,
        subject_user_id=ic2.id,
        base=Decimal("70000"),
        variable=Decimal("14000"),
        lti_fmv=Decimal("7000"),
        lti_units=7,
    )
    await db.flush()

    # CFO root allocation: SUBMITTED, with one line for the MoM.
    # The line's allocated_amount is the MoM's total_pool. Sized so
    # MoM has enough budget to JVRE-align its lines without overrun.
    # MoP-pool JVRE = sum(subtree leaves JVRE) = mop_total + ic1_total
    # + ic2_total = 200000 + 104000 + 91000 = 395000.
    # MoM pool = 1.10 * cushion = 434500.
    mom_total_pool = Decimal("434500")
    cfo_alloc = BudgetAllocation(
        tenant_id=tenant.id,
        cycle_id=cycle.id,
        owner_user_id=cfo.id,
        parent_allocation_id=None,
        total_pool=mom_total_pool,
        strategic_reserve=Decimal("0"),
        budget_for_allocation=mom_total_pool,
        currency_code="USD",
        status=BudgetAllocationStatus.SUBMITTED.value,
        submitted_at=datetime.now(UTC),
        submitted_by_user_id=cfo.id,
    )
    db.add(cfo_alloc)
    await db.flush()
    db.add(
        BudgetAllocationLine(
            allocation_id=cfo_alloc.id,
            recipient_user_id=mom.id,
            allocated_amount=mom_total_pool,
            base_pool=Decimal("282425"),  # 65%
            variable_pool=Decimal("86900"),  # 20%
            lti_grant_fmv_pool=Decimal("43450"),  # 10%
            reserve_pool=Decimal("21725"),  # 5%
            jvre_rec_amount=Decimal("395000"),
            currency_code="USD",
        )
    )
    await db.flush()

    # MoM allocation: PENDING, no lines yet — Phase 4's
    # align-with-jvre creates them.
    mom_alloc = BudgetAllocation(
        tenant_id=tenant.id,
        cycle_id=cycle.id,
        owner_user_id=mom.id,
        parent_allocation_id=cfo_alloc.id,
        total_pool=mom_total_pool,
        strategic_reserve=Decimal("0"),
        budget_for_allocation=mom_total_pool,
        currency_code="USD",
        status=BudgetAllocationStatus.PENDING.value,
    )
    db.add(mom_alloc)
    await db.flush()

    await db.commit()

    return E2ETree(
        tenant=tenant,
        cycle=cycle,
        cfo=cfo,
        mom=mom,
        mop=mop,
        ic1=ic1,
        ic2=ic2,
        cfo_alloc=cfo_alloc,
        mom_alloc=mom_alloc,
    )
