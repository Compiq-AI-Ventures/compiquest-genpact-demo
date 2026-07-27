"""Seed the ``oscorp`` demo tenant for the v0.1 JVRE workspace.

Idempotent: drops the ``oscorp`` tenant if it exists, then recreates the
entire org tree + cycle + JVRE snapshots + budget allocations from
scratch. Re-running gives byte-identical state — every UUID is fresh
but every numeric value, every relationship, every distribution is
deterministic (seeded RNG). Demos are reproducible.

Spider-Man casting
------------------
The tenant is Oscorp Industries, the universe's catch-all corporate
fixture. Personas are drawn from the Spider-Man comics so the demo
is memorable + the org tree reads like a who's who of Earth-616:

* CFO   — Norman Osborn (he runs the company; budget owner)
* CHRO  — Liz Allan (corporate-side; runs Allan Chemicals in canon)
* C&B   — Spencer Smythe (Comp & Benefits; inventor of the Spider-Slayer bots)
* MoMs — Otto Octavius, Curt Connors, Quentin Beck, Adrian Toomes
         (the four scientists / engineers most likely to head a tech
         business unit if their evil hobbies hadn't gotten in the way)
* MoPs — 16 supporting villains and antiheroes from the rogue's gallery
* ICs  — supporting cast: Spider-People, Daily Bugle staff, science
         peers, and the occasional reformed villain

Org tree
--------
* 1 CFO   — owns the root budget allocation (``cfo@oscorp.example.com``)
* 1 CHRO  — read access across the cycle (``chro@oscorp.example.com``)
* 1 C&B   — comp & benefits analyst (``cnb@oscorp.example.com``)
* 4 MoMs  — one per business line (Engineering / Product / Cyber Security
            / Quality Assurance)
* 16 MoPs — 4 per MoM
* 88 ICs  — variable per MoP (4, 5, 6, 7 in rotation), totaling 88

Total: ~110 users — within the spec target of 80-120.

What lands per user
-------------------
* User row + role grant (CFO / CHRO / MANAGER_OF_MANAGERS / MANAGER /
  no extra role for ICs).
* Reporting relationship (skipped for CFO and CHRO).
* Compensation history rows for ``FY2024`` and ``FY2023``.
* Market benchmark (skipped for CFO and CHRO).
* JVRE snapshot for the active cycle, with criticality / market
  position / promotion readiness distributed across all three buckets
  so the screen chips aren't monochrome.

What lands at the cycle level
-----------------------------
* One ``CompensationCycle`` row, status ACTIVE, currency USD.
* CFO's root ``BudgetAllocation`` (status SUBMITTED) + one
  ``BudgetAllocationLine`` per MoM. The CFO has "already submitted"
  so the demo can begin at the MoM screen.
* Per-MoM ``BudgetAllocation`` (status PENDING) — the MoM workflow
  picks up from here.

No pay recommendations are seeded. They get created on the first
"Save & Next" click in the MoP / MoM workflow, exactly as in real
life — the seed mirrors a fresh comp cycle that's been opened and
funded but not yet acted on.

Credentials
-----------
Every demo user shares the password ``oscorp-demo-12345``. The password
is hashed once and the resulting hash string is reused across all
users so seeding doesn't pay the bcrypt tax 110 times. This is a
demo-only shortcut; never do it in production.

Run
---

    uv run python -m scripts.seed_demo_tenant
"""

from __future__ import annotations

import argparse
import asyncio
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from app.core.database import AsyncSessionLocal
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
from app.models.compensation_history import CompensationHistory
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
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user_role import UserRole
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TENANT_CODE = "oscorp"
TENANT_NAME = "Oscorp Industries"
TENANT_DOMAIN = "oscorp.example.com"
DEMO_PASSWORD = "oscorp-demo-12345"
DEFAULT_CURRENCY = "USD"

CYCLE_FY = "FY2026"
CYCLE_PRIOR_FYS = ("FY2024", "FY2023")  # for compensation_history seeding

# Department names per MoM. Indexed; later loops use this list as the
# canonical source.
DEPARTMENTS: tuple[str, ...] = (
    "Engineering",
    "Product",
    "Cyber Security",
    "Quality Assurance",
)

# Spider-Man universe casting. The four MoMs are scientists / engineers
# who, in canon, head (or could plausibly head) a tech business unit at
# Oscorp. Mapped to departments by index: Otto → Engineering, Connors
# → Product (biotech), Beck → Cyber Security (illusion / FX),
# Toomes → Quality Assurance (electronics manufacturing).
MOM_NAMES: tuple[tuple[str, str], ...] = (
    ("Otto", "Octavius"),       # Doc Ock — Engineering
    ("Curt", "Connors"),        # The Lizard — Product / Biotech
    ("Quentin", "Beck"),        # Mysterio — Cyber Security
    ("Adrian", "Toomes"),       # Vulture — Quality Assurance
)
# 16 mid-tier rogues + occasional anti-heroes who fit a manager role.
MOP_NAMES: tuple[tuple[str, str], ...] = (
    ("Eddie", "Brock"),         # Venom
    ("Felicia", "Hardy"),       # Black Cat
    ("Anne", "Weying"),         # She-Venom (corporate lawyer in canon)
    ("Mac", "Gargan"),          # Scorpion
    ("Herman", "Schultz"),      # Shocker
    ("Phineas", "Mason"),       # The Tinkerer
    ("Roderick", "Kingsley"),   # Hobgoblin
    ("Morris", "Bench"),        # Hydro-Man
    ("Aleksei", "Sytsevich"),   # Rhino
    ("Cletus", "Kasady"),       # Carnage
    ("Sergei", "Kravinoff"),    # Kraven the Hunter
    ("Dmitri", "Smerdyakov"),   # Chameleon
    ("Max", "Dillon"),          # Electro
    ("Mark", "Raxton"),         # Molten Man
    ("Cassandra", "Webb"),      # Madame Web
    ("Yuri", "Watanabe"),       # NYPD Captain (Wraith)
)
# IC pool: Spider-People, Daily Bugle staff, supporting science peers,
# and minor recurring characters. Cycled with a small offset per MoP so
# two MoPs don't end up with the same first names in the same dept.
IC_FIRST_NAMES: tuple[str, ...] = (
    "Peter", "Miles", "Gwen", "Mary", "Ben", "Anya", "Cindy",
    "Jessica", "Julia", "Kaine", "Pavitr", "Hobie", "Margo",
    "Jefferson", "Rio", "Betty", "Carlie", "Ned", "Robbie",
    "Glory", "Sara", "Ezekiel", "Madame", "Anna", "Liz",
    "Jean", "May", "Joe",
)
IC_LAST_NAMES: tuple[str, ...] = (
    "Parker", "Morales", "Stacy", "Watson", "Reilly", "Brant",
    "Cooper", "Leeds", "Robertson", "Cassidy", "Drew", "Carpenter",
    "Bromwell", "Forrester", "MacKenzie", "DeWolff",
)

# ICs per MoP, cycled. Average 5.5 across the 16 MoPs = 88 total ICs.
IC_COUNT_PER_MOP: tuple[int, ...] = (4, 5, 6, 7)

# IC level distribution per MoP. Cycled per IC index. Captures the
# usual pyramid (more juniors than seniors).
IC_LEVELS: tuple[str, ...] = ("L1", "L2", "L2", "L3", "L3", "L4", "L5")

# Salary bands by level (annual, USD). Bottom of band = junior;
# top = senior-in-level. Used to derive base / variable / LTI.
SALARY_BAND: dict[str, tuple[int, int]] = {
    "L1": (40_000, 55_000),
    "L2": (60_000, 80_000),
    "L3": (80_000, 100_000),
    "L4": (110_000, 140_000),
    "L5": (150_000, 200_000),
    "M1": (130_000, 180_000),  # MoP
    "M2": (180_000, 240_000),  # MoM
    "E1": (300_000, 450_000),  # Exec (CFO / CHRO)
}

# Variable pay as a fraction of base. Higher for senior + exec roles.
VARIABLE_RATIO: dict[str, tuple[float, float]] = {
    "L1": (0.10, 0.15),
    "L2": (0.10, 0.18),
    "L3": (0.15, 0.22),
    "L4": (0.18, 0.25),
    "L5": (0.22, 0.30),
    "M1": (0.25, 0.35),
    "M2": (0.30, 0.45),
    "E1": (0.40, 0.60),
}

# LTI grant FMV by level. Tuple is (low, high, units_low, units_high).
LTI_GRANT: dict[str, tuple[int, int, int, int]] = {
    "L1": (0, 0, 0, 0),
    "L2": (0, 5_000, 0, 5),
    "L3": (4_000, 12_000, 4, 12),
    "L4": (8_000, 22_000, 8, 22),
    "L5": (15_000, 35_000, 15, 35),
    "M1": (20_000, 45_000, 20, 45),
    "M2": (35_000, 80_000, 35, 80),
    "E1": (80_000, 200_000, 80, 200),
}

# Reserve range recommendations by manager tier — exposed via the API
# for the strategic-reserve slider on the Budget Planner.
MOM_RESERVE_RANGE_PCT = (Decimal("0.10"), Decimal("0.13"))
MOP_RESERVE_RANGE_PCT = (Decimal("0.04"), Decimal("0.08"))


# ---------------------------------------------------------------------------
# Helpers / lightweight value objects
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PayPackage:
    """Computed pay components for one user."""

    base: Decimal
    variable: Decimal
    lti_fmv: Decimal
    lti_units: int

    @property
    def tcc(self) -> Decimal:
        return self.base + self.variable

    @property
    def total_rewards(self) -> Decimal:
        return self.tcc + self.lti_fmv


@dataclass
class SeededUser:
    """Bookkeeping for a seeded user and their payload."""

    user: User
    level: str
    pay: PayPackage
    role_code: str | None  # None for ICs
    department: str | None  # None for CFO / CHRO


@dataclass
class OrgTree:
    cfo: SeededUser
    chro: SeededUser
    cnb: SeededUser
    moms: list[SeededUser] = field(default_factory=list)
    # Indexed [mom_idx] -> list of MoPs under that MoM.
    mops_by_mom: list[list[SeededUser]] = field(default_factory=list)
    # Indexed [mom_idx][mop_idx] -> list of ICs under that MoP.
    ics_by_mop: list[list[list[SeededUser]]] = field(default_factory=list)

    @property
    def all_managers(self) -> list[SeededUser]:
        return self.moms + [m for tier in self.mops_by_mom for m in tier]

    @property
    def all_ics(self) -> list[SeededUser]:
        return [
            ic
            for mom_tier in self.ics_by_mop
            for mop_tier in mom_tier
            for ic in mop_tier
        ]

    @property
    def all_subjects(self) -> list[SeededUser]:
        """Every user who's a recommendation subject (i.e. has a JVRE
        snapshot)."""
        return self.all_managers + self.all_ics


def _email(slug: str) -> str:
    return f"{slug}@{TENANT_DOMAIN}"


def _money_in_band(rng: random.Random, band: tuple[int, int]) -> Decimal:
    low, high = band
    if low == high == 0:
        return Decimal("0")
    return Decimal(rng.randint(low, high))


def _round_pay(amount: Decimal) -> Decimal:
    """Round to nearest $100 so seeded numbers look natural on screen."""
    return (amount / Decimal("100")).quantize(Decimal("1")) * Decimal("100")


def _make_pay(rng: random.Random, level: str) -> PayPackage:
    base = _round_pay(_money_in_band(rng, SALARY_BAND[level]))
    var_low, var_high = VARIABLE_RATIO[level]
    var_ratio = Decimal(str(rng.uniform(var_low, var_high))).quantize(
        Decimal("0.01")
    )
    variable = _round_pay(base * var_ratio)
    lti_low, lti_high, units_low, units_high = LTI_GRANT[level]
    lti_fmv = _round_pay(_money_in_band(rng, (lti_low, lti_high)))
    units = (
        rng.randint(units_low, units_high) if units_high > 0 else 0
    )
    return PayPackage(base=base, variable=variable, lti_fmv=lti_fmv, lti_units=units)


# Distribute the chip values deterministically rather than randomly so
# every seed run produces an identical mix.
def _criticality_for(idx: int) -> str:
    return (
        JvreCriticality.CRITICAL.value,
        JvreCriticality.MODERATE_HIGH.value,
        JvreCriticality.MODERATE_HIGH.value,
        JvreCriticality.LOW_RISK.value,
    )[idx % 4]


def _market_position_for(idx: int) -> str:
    return (
        JvreMarketPosition.BELOW_MARKET.value,
        JvreMarketPosition.MARKET_ALIGNED.value,
        JvreMarketPosition.MARKET_ALIGNED.value,
        JvreMarketPosition.ABOVE_MARKET.value,
    )[idx % 4]


def _promotion_readiness_for(idx: int) -> str:
    return (
        JvrePromotionReadiness.READY.value,
        JvrePromotionReadiness.CANDIDATE.value,
        JvrePromotionReadiness.NOT_READY.value,
        JvrePromotionReadiness.NOT_READY.value,
    )[idx % 4]


def _next_level(level: str) -> str:
    """Recommended target level for a 'Ready' subject."""
    progression = {
        "L1": "L2",
        "L2": "L3",
        "L3": "L4",
        "L4": "L5",
        "L5": "L5",  # senior IC stays at L5; promotions out are M1
        "M1": "M2",
        "M2": "M2",
    }
    return progression.get(level, level)


# JVRE score ranges anchored to real retention-risk signals.
# Higher score = stronger value-to-retain / higher flight risk.
_JVRE_SCORE_RANGE: dict[tuple[str, str], tuple[float, float]] = {
    (JvreCriticality.CRITICAL.value, JvreMarketPosition.BELOW_MARKET.value): (7.5, 9.8),
    (JvreCriticality.MODERATE_HIGH.value, JvreMarketPosition.MARKET_ALIGNED.value): (4.0, 7.0),
    (JvreCriticality.LOW_RISK.value, JvreMarketPosition.ABOVE_MARKET.value): (1.5, 4.5),
}

# Growth rate ranges driven by market position — BELOW_MARKET gets a
# larger lift to close the gap; ABOVE_MARKET gets a modest premium top-up.
_GROWTH_RATE_RANGE: dict[str, tuple[float, float]] = {
    JvreMarketPosition.BELOW_MARKET.value: (0.09, 0.14),
    JvreMarketPosition.MARKET_ALIGNED.value: (0.05, 0.09),
    JvreMarketPosition.ABOVE_MARKET.value: (0.03, 0.06),
}


def _jvre_score_for(
    criticality: str,
    market_position: str,
    promotion_readiness: str,
    rng: random.Random,
) -> Decimal:
    lo, hi = _JVRE_SCORE_RANGE.get((criticality, market_position), (3.0, 6.0))
    # READY subjects are harder to replace — nudge score toward the top of the band.
    if promotion_readiness == JvrePromotionReadiness.READY.value:
        lo = min(lo + 0.8, hi)
    return Decimal(str(rng.uniform(lo, hi))).quantize(Decimal("0.01"))


def _growth_rate_for(market_position: str, rng: random.Random) -> Decimal:
    lo, hi = _GROWTH_RATE_RANGE.get(market_position, (0.05, 0.09))
    return Decimal(str(rng.uniform(lo, hi))).quantize(Decimal("0.001"))


def _ai_suggestion_text(market_position: str, growth_pct: int) -> str:
    if market_position == JvreMarketPosition.BELOW_MARKET.value:
        return (
            f"Market deficit identified; recommended +{growth_pct}% to restore"
            " competitive parity and reduce near-term attrition risk."
        )
    if market_position == JvreMarketPosition.ABOVE_MARKET.value:
        return (
            f"Above-market positioning maintained; +{growth_pct}% sustains the"
            " premium that reflects role scarcity and demonstrated impact."
        )
    return (
        f"Recommended +{growth_pct}% to sustain market alignment through the"
        " next review cycle."
    )


# ---------------------------------------------------------------------------
# Build phase
# ---------------------------------------------------------------------------
async def _drop_tenant_if_exists(db: AsyncSession) -> None:
    existing = (
        await db.execute(select(Tenant).where(Tenant.code == TENANT_CODE))
    ).scalar_one_or_none()
    if existing is None:
        return
    # Cascade walks: tenant → users → user_roles + every tenant-scoped
    # business table including the new JVRE-workspace ones.
    await db.execute(delete(Tenant).where(Tenant.id == existing.id))
    await db.flush()


async def _resolve_role(db: AsyncSession, code: str) -> Role:
    role = (
        await db.execute(select(Role).where(Role.code == code))
    ).scalar_one_or_none()
    if role is None:
        raise RuntimeError(
            f"Role {code!r} not found. Is the JVRE workspace migration "
            f"applied? Run `uv run alembic upgrade head` first."
        )
    return role


async def _build_users(
    db: AsyncSession,
    *,
    tenant: Tenant,
    rng: random.Random,
    shared_password_hash: str,
) -> OrgTree:
    """Materialize the entire org tree and the role grants for it."""

    # Cache role lookups.
    role_by_code: dict[str, Role] = {}
    for code in (
        "CFO",
        "CHRO",
        "C_AND_B",
        "MANAGER_OF_MANAGERS",
        "MANAGER",
        "IC",
    ):
        role_by_code[code] = await _resolve_role(db, code)

    def _new_user(
        slug: str, first: str, last: str
    ) -> User:
        return User(
            tenant_id=tenant.id,
            email=_email(slug),
            password_hash=shared_password_hash,
            first_name=first,
            last_name=last,
        )

    # CFO + CHRO + C&B ---------------------------------------------------
    # Norman Osborn runs Oscorp; Liz Allan runs Allan Chemicals in
    # canon, which makes her a plausible HR-side exec at the parent
    # company. Spencer Smythe (inventor of the Spider-Slayer robots) is
    # repurposed here as the C&B analyst — technically gifted, morally
    # flexible, perfect for compensation work.
    cfo_user = _new_user("cfo", "Norman", "Osborn")
    chro_user = _new_user("chro", "Liz", "Allan")
    cnb_user = _new_user("cnb", "Spencer", "Smythe")
    db.add(cfo_user)
    db.add(chro_user)
    db.add(cnb_user)
    await db.flush()

    db.add(UserRole(user_id=cfo_user.id, role_id=role_by_code["CFO"].id))
    db.add(UserRole(user_id=chro_user.id, role_id=role_by_code["CHRO"].id))
    db.add(UserRole(user_id=cnb_user.id, role_id=role_by_code["C_AND_B"].id))

    cfo = SeededUser(
        user=cfo_user,
        level="E1",
        pay=_make_pay(rng, "E1"),
        role_code="CFO",
        department=None,
    )
    chro = SeededUser(
        user=chro_user,
        level="E1",
        pay=_make_pay(rng, "E1"),
        role_code="CHRO",
        department=None,
    )
    cnb = SeededUser(
        user=cnb_user,
        level="E1",
        pay=_make_pay(rng, "E1"),
        role_code="C_AND_B",
        department=None,
    )

    # MoMs ---------------------------------------------------------------
    moms: list[SeededUser] = []
    for mom_idx, (first, last) in enumerate(MOM_NAMES):
        slug = f"mom{mom_idx + 1}"
        u = _new_user(slug, first, last)
        db.add(u)
        await db.flush()
        db.add(
            UserRole(
                user_id=u.id, role_id=role_by_code["MANAGER_OF_MANAGERS"].id
            )
        )
        moms.append(
            SeededUser(
                user=u,
                level="M2",
                pay=_make_pay(rng, "M2"),
                role_code="MANAGER_OF_MANAGERS",
                department=DEPARTMENTS[mom_idx],
            )
        )

    # MoPs ---------------------------------------------------------------
    mops_by_mom: list[list[SeededUser]] = []
    mop_iter = iter(MOP_NAMES)
    for mom_idx, mom in enumerate(moms):
        bucket: list[SeededUser] = []
        for mop_idx in range(4):
            first, last = next(mop_iter)
            slug = f"mop{mom_idx + 1}-{mop_idx + 1}"
            u = _new_user(slug, first, last)
            db.add(u)
            await db.flush()
            db.add(UserRole(user_id=u.id, role_id=role_by_code["MANAGER"].id))
            bucket.append(
                SeededUser(
                    user=u,
                    level="M1",
                    pay=_make_pay(rng, "M1"),
                    role_code="MANAGER",
                    department=mom.department,
                )
            )
        mops_by_mom.append(bucket)

    # ICs — granted IC so they can log in and be tested against
    # role-gated endpoints; still no self-service comp-data workspace. ---
    ics_by_mop: list[list[list[SeededUser]]] = []
    for mom_idx, mop_tier in enumerate(mops_by_mom):
        per_mom: list[list[SeededUser]] = []
        for mop_idx, mop in enumerate(mop_tier):
            ic_count = IC_COUNT_PER_MOP[mop_idx % len(IC_COUNT_PER_MOP)]
            ic_bucket: list[SeededUser] = []
            for ic_idx in range(ic_count):
                # Spread first-name pool by an offset so IC names don't
                # collide with the MoP/MoM pool used above.
                first = IC_FIRST_NAMES[
                    (mom_idx * 11 + mop_idx * 7 + ic_idx)
                    % len(IC_FIRST_NAMES)
                ]
                last = IC_LAST_NAMES[
                    (mom_idx * 5 + mop_idx * 13 + ic_idx)
                    % len(IC_LAST_NAMES)
                ]
                slug = f"ic{mom_idx + 1}-{mop_idx + 1}-{ic_idx + 1}"
                level = IC_LEVELS[ic_idx % len(IC_LEVELS)]
                u = _new_user(slug, first, last)
                db.add(u)
                await db.flush()
                db.add(UserRole(user_id=u.id, role_id=role_by_code["IC"].id))
                ic_bucket.append(
                    SeededUser(
                        user=u,
                        level=level,
                        pay=_make_pay(rng, level),
                        role_code=None,
                        department=mop.department,
                    )
                )
            per_mom.append(ic_bucket)
        ics_by_mop.append(per_mom)
    await db.flush()

    return OrgTree(
        cfo=cfo,
        chro=chro,
        cnb=cnb,
        moms=moms,
        mops_by_mom=mops_by_mom,
        ics_by_mop=ics_by_mop,
    )


async def _create_cycle(
    db: AsyncSession, *, tenant: Tenant, cfo: SeededUser
) -> CompensationCycle:
    cycle = CompensationCycle(
        tenant_id=tenant.id,
        fy_label=CYCLE_FY,
        status=CompensationCycleStatus.ACTIVE.value,
        currency_code=DEFAULT_CURRENCY,
        created_by_user_id=cfo.user.id,
    )
    db.add(cycle)
    await db.flush()
    return cycle


async def _create_reporting_relationships(
    db: AsyncSession,
    *,
    tenant: Tenant,
    cycle: CompensationCycle,
    org: OrgTree,
) -> None:
    """Populate manager → report edges for the active cycle."""
    # MoM → MoP
    for mom_idx, mom in enumerate(org.moms):
        for mop in org.mops_by_mom[mom_idx]:
            db.add(
                ReportingRelationship(
                    tenant_id=tenant.id,
                    cycle_id=cycle.id,
                    manager_user_id=mom.user.id,
                    report_user_id=mop.user.id,
                )
            )
    # MoP → IC
    for mom_idx, mop_tier in enumerate(org.mops_by_mom):
        for mop_idx, mop in enumerate(mop_tier):
            for ic in org.ics_by_mop[mom_idx][mop_idx]:
                db.add(
                    ReportingRelationship(
                        tenant_id=tenant.id,
                        cycle_id=cycle.id,
                        manager_user_id=mop.user.id,
                        report_user_id=ic.user.id,
                    )
                )
    await db.flush()


async def _create_compensation_history(
    db: AsyncSession,
    *,
    tenant: Tenant,
    org: OrgTree,
    rng: random.Random,
) -> None:
    """Populate two prior FYs of compensation history per subject."""
    for subject in [*org.all_subjects, org.cfo, org.chro, org.cnb]:
        prior = subject.pay.base
        for fy in CYCLE_PRIOR_FYS:
            # Roughly 5-10% raise vs the year before; back-compute.
            growth = Decimal(str(rng.uniform(0.05, 0.10))).quantize(
                Decimal("0.01")
            )
            prior_year_base = _round_pay(prior / (Decimal("1") + growth))
            change = prior - prior_year_base
            db.add(
                CompensationHistory(
                    tenant_id=tenant.id,
                    subject_user_id=subject.user.id,
                    fy_label=fy,
                    level_code=subject.level,
                    comp_change_amount=change,
                    currency_code=DEFAULT_CURRENCY,
                    perf_rating=rng.choice(("3/5", "4/5", "4/5", "5/5")),
                    was_promoted=False,
                )
            )
            prior = prior_year_base
    await db.flush()


async def _create_market_benchmarks(
    db: AsyncSession,
    *,
    tenant: Tenant,
    org: OrgTree,
    rng: random.Random,
) -> None:
    """Populate one benchmark per IC + per Manager (skip exec tier)."""
    for subject in org.all_subjects:
        # Target pay = base * multiplier (sometimes above, sometimes
        # below current). Give the screen something to render.
        multiplier = Decimal(str(rng.uniform(0.90, 1.20))).quantize(
            Decimal("0.01")
        )
        target = _round_pay(subject.pay.base * multiplier)
        compa = (subject.pay.base / target).quantize(Decimal("0.01"))
        delta_pct = ((subject.pay.base - target) / target * Decimal("100")).quantize(
            Decimal("1")
        )
        if delta_pct < 0:
            delta_text = f"Under Target by {abs(int(delta_pct))}%"
        elif delta_pct > 0:
            delta_text = f"Above Target by {int(delta_pct)}%"
        else:
            delta_text = "Aligned with Target"
        db.add(
            MarketBenchmark(
                tenant_id=tenant.id,
                subject_user_id=subject.user.id,
                current_pay=subject.pay.base,
                target_pay=target,
                currency_code=DEFAULT_CURRENCY,
                compa_ratio=compa,
                target_compa_ratio_min=Decimal("0.95"),
                target_compa_ratio_max=Decimal("1.05"),
                delta_status_text=delta_text,
            )
        )
    await db.flush()


async def _create_jvre_snapshots(
    db: AsyncSession,
    *,
    tenant: Tenant,
    cycle: CompensationCycle,
    org: OrgTree,
    rng: random.Random,
) -> None:
    """One JVRE snapshot per (cycle, subject), distributed across the
    chip buckets for visual variety on screen."""
    for idx, subject in enumerate(org.all_subjects):
        criticality = _criticality_for(idx)
        market_pos = _market_position_for(idx)
        promo = _promotion_readiness_for(idx)

        # Growth rate driven by market position — BELOW_MARKET closes a gap,
        # ABOVE_MARKET receives a modest premium top-up.
        growth = _growth_rate_for(market_pos, rng)
        rec_base = _round_pay(subject.pay.base * (Decimal("1") + growth))
        rec_variable = _round_pay(
            subject.pay.variable * (Decimal("1") + growth)
        )
        rec_lti = _round_pay(subject.pay.lti_fmv * (Decimal("1") + growth))
        rec_units = subject.pay.lti_units  # units don't grow with %

        rec_level = (
            _next_level(subject.level)
            if promo == JvrePromotionReadiness.READY.value
            else subject.level
        )

        # risk_callout_text only applies to managers who have direct reports.
        # ICs (role_code is None) never carry "reports flagged" text.
        risk_text = None
        if criticality == JvreCriticality.CRITICAL.value and subject.role_code is not None:
            team_size = 4 if subject.role_code == "MANAGER_OF_MANAGERS" else rng.randint(5, 7)
            flagged = rng.randint(1, max(1, team_size // 3))
            risk_text = (
                f"{flagged} of {team_size} reports flagged"
                " for retention risk; intervention recommended."
            )

        growth_pct = int(growth * 100)
        ai_text = _ai_suggestion_text(market_pos, growth_pct)

        # Score derived from criticality + market position + promotion readiness
        # so it is internally consistent with the chip signals on the card.
        jvre_score = _jvre_score_for(criticality, market_pos, promo, rng)
        db.add(
            JvreSnapshot(
                tenant_id=tenant.id,
                cycle_id=cycle.id,
                subject_user_id=subject.user.id,
                current_base=subject.pay.base,
                current_variable=subject.pay.variable,
                current_fy_vesting_units=subject.pay.lti_units,
                jvre_score=jvre_score,
                recommended_base=rec_base,
                recommended_variable=rec_variable,
                recommended_lti_fmv=rec_lti,
                recommended_lti_units=rec_units,
                recommended_other_rewards=Decimal("0"),
                currency_code=DEFAULT_CURRENCY,
                criticality=criticality,
                market_position=market_pos,
                promotion_readiness=promo,
                recommended_level=rec_level,
                risk_callout_text=risk_text,
                ai_suggestion_text=ai_text,
            )
        )
    await db.flush()


def _build_rationale_text(
    subject_name: str,
    job_title: str,
    current_base: Decimal,
    rec_base: Decimal,
    criticality: str,
    market_position: str,
    promotion_readiness: str,
    jvre_score: Decimal,
) -> str:
    """Generate a professional compensation rationale from JVRE signals."""
    growth_pct = int(((rec_base - current_base) / current_base * 100).quantize(Decimal("1")))
    score_f = float(jvre_score)

    if score_f >= 7.5:
        score_context = (
            f"a JVRE score of {jvre_score}/10 — placing {subject_name.split()[0]} "
            f"among the strongest value-retention profiles in this cycle"
        )
    elif score_f >= 5.0:
        score_context = (
            f"a JVRE score of {jvre_score}/10, indicating a solid retention-value "
            f"profile with meaningful upside risk if left unaddressed"
        )
    elif score_f >= 3.0:
        score_context = (
            f"a JVRE score of {jvre_score}/10, reflecting moderate retention risk "
            f"at the current compensation level"
        )
    else:
        score_context = (
            f"a JVRE score of {jvre_score}/10, suggesting limited near-term flight "
            f"risk under current market conditions"
        )

    market_map = {
        JvreMarketPosition.BELOW_MARKET.value: (
            "Currently positioned below market benchmarks, this adjustment directly "
            "closes the compensation gap and reduces the risk of attrition to competitors."
        ),
        JvreMarketPosition.MARKET_ALIGNED.value: (
            "This adjustment maintains market alignment, ensuring the offer remains "
            "competitive as external benchmarks continue to move."
        ),
        JvreMarketPosition.ABOVE_MARKET.value: (
            "Already positioned above market, this moderate increase sustains the "
            "premium that reflects the scarcity and impact of the role."
        ),
    }
    criticality_map = {
        JvreCriticality.CRITICAL.value: (
            f"{subject_name.split()[0]}'s role carries critical organizational weight — "
            f"the cost and disruption of replacement significantly exceeds this investment."
        ),
        JvreCriticality.MODERATE_HIGH.value: (
            f"The role holds moderate-to-high strategic importance; sustaining "
            f"{subject_name.split()[0]}'s engagement protects key delivery capacity."
        ),
        JvreCriticality.LOW_RISK.value: (
            f"While replacement risk is lower in this role, recognizing "
            f"{subject_name.split()[0]}'s contributions reinforces retention norms across the team."
        ),
    }
    promo_map = {
        JvrePromotionReadiness.READY.value: (
            f"{subject_name.split()[0]} has demonstrated readiness for the next level. "
            f"A promotion should be actioned in parallel with this compensation adjustment "
            f"to reflect the expanded scope and prevent internal equity issues."
        ),
        JvrePromotionReadiness.CANDIDATE.value: (
            f"{subject_name.split()[0]} is tracking toward promotion eligibility. "
            f"This increase is structured to remain defensible at the current level "
            f"while creating headroom for the promotion cycle."
        ),
        JvrePromotionReadiness.NOT_READY.value: (
            f"{subject_name.split()[0]} is not being considered for promotion this cycle. "
            f"The adjustment is scoped to recognize in-role performance and manage "
            f"market drift without signaling a level change."
        ),
    }

    market_stmt = market_map.get(
        market_position, market_position.replace("_", " ").capitalize() + "."
    )
    crit_stmt = criticality_map.get(
        criticality, criticality.replace("_", " ").capitalize() + "."
    )
    promo_stmt = promo_map.get(
        promotion_readiness, promotion_readiness.replace("_", " ").capitalize() + "."
    )

    return (
        f"A {growth_pct}% base salary adjustment is recommended for {subject_name}, "
        f"{job_title}, bringing total base compensation to ${int(rec_base):,}. "
        f"This recommendation is informed by {score_context}.\n\n"
        f"{market_stmt}\n\n"
        f"{crit_stmt}\n\n"
        f"{promo_stmt}"
    )


async def _create_jvre_rationale(
    db: AsyncSession,
    *,
    tenant: Tenant,
    cycle: CompensationCycle,
    org: OrgTree,
) -> None:
    """Seed one pre-generated rationale row per subject using snapshot data."""
    from sqlalchemy import select as sa_select

    snapshots_result = await db.execute(
        sa_select(JvreSnapshot).where(
            JvreSnapshot.tenant_id == tenant.id,
            JvreSnapshot.cycle_id == cycle.id,
        )
    )
    snapshot_by_subject = {s.subject_user_id: s for s in snapshots_result.scalars().all()}

    for subject in org.all_subjects:
        snap = snapshot_by_subject.get(subject.user.id)
        if snap is None or snap.current_base is None or snap.recommended_base is None:
            continue

        full_name = f"{subject.user.first_name} {subject.user.last_name or ''}".strip()
        job_title = subject.user.job_title or subject.role_code or "Team Member"

        rationale = _build_rationale_text(
            subject_name=full_name,
            job_title=job_title,
            current_base=snap.current_base,
            rec_base=snap.recommended_base,
            criticality=snap.criticality or JvreCriticality.LOW_RISK.value,
            market_position=snap.market_position or JvreMarketPosition.MARKET_ALIGNED.value,
            promotion_readiness=snap.promotion_readiness or JvrePromotionReadiness.NOT_READY.value,
            jvre_score=snap.jvre_score or Decimal("5.00"),
        )

        db.add(
            JvreRationale(
                tenant_id=tenant.id,
                cycle_id=cycle.id,
                subject_user_id=subject.user.id,
                rationale_text=rationale,
                model_id="seeded",
            )
        )
    await db.flush()


_JVRE_TIER_THRESHOLDS = [(Decimal("7"), "HIGH"), (Decimal("4"), "MODERATE")]
_SEED_DATE = date(2026, 6, 11)
_USD_TO_INR = Decimal("83")
_DEPT_MAP = {
    "ENG": "Engineering", "PROD": "Product / Biotech",
    "SEC": "Cyber Security", "QA": "Quality Assurance",
    "FIN": "Finance", "HR": "Human Resources",
}
_BONUS_PCT = Decimal("0.12")


def _jvre_tier(score: Decimal) -> str:
    for threshold, label in _JVRE_TIER_THRESHOLDS:
        if score >= threshold:
            return label
    return "LOW"


_SENIOR_LEVEL_MARKERS = frozenset(("SENIOR", "STAFF", "PRINCIPAL", "VP", "DIR", "L5", "L6"))


def _is_senior_level(level: str | None) -> bool:
    return any(m in (level or "").upper() for m in _SENIOR_LEVEL_MARKERS)


def _rnd(rng: random.Random, lo: float, hi: float, places: str = "0.001") -> Decimal:
    return Decimal(str(rng.uniform(lo, hi))).quantize(Decimal(places))


def _build_engine_row(
    *,
    idx: int,
    subject: SeededUser,
    snap: JvreSnapshot,
    bench: object | None,
    cycle: CompensationCycle,
    tenant: Tenant,
    rng: random.Random,
    supervisor_name: str | None = None,
) -> IquestEngineOutput:
    current_base = snap.current_base or subject.pay.base
    rec_base = snap.recommended_base or current_base
    jvre_score = snap.jvre_score or Decimal("5.00")
    rec_increase = (
        ((rec_base - current_base) / current_base).quantize(Decimal("0.0001"))
        if current_base
        else Decimal("0")
    )
    total_cash = (current_base * (Decimal("1") + _BONUS_PCT)).quantize(Decimal("1"))
    rec_total_cash = (rec_base * (Decimal("1") + _BONUS_PCT)).quantize(Decimal("1"))
    p50 = getattr(bench, "target_pay", current_base) or current_base
    p25 = (p50 * Decimal("0.85")).quantize(Decimal("1"))
    p75 = (p50 * Decimal("1.15")).quantize(Decimal("1"))
    external_cr = getattr(bench, "compa_ratio", None) or Decimal("1.00")
    promotion_flag = snap.promotion_readiness == JvrePromotionReadiness.READY.value
    full_name = f"{subject.user.first_name} {subject.user.last_name or ''}".strip()
    dept_name = _DEPT_MAP.get(subject.department or "ENG", subject.department or "ENG")
    job_title = subject.user.job_title or subject.role_code or "Team Member"

    # Derived temporal and equity fields
    tenure_yrs = _rnd(rng, 0.5, 8, "0.1")
    doj = _SEED_DATE - timedelta(days=int(float(tenure_yrs) * 365))
    months_to_vest = _rnd(rng, 3, 18, "0.1")
    next_vest = _SEED_DATE + timedelta(days=int(float(months_to_vest) * 30))
    unvested_usd = _rnd(rng, 25000, 120000) if _is_senior_level(subject.level) else _rnd(rng, 5000, 40000)
    equity_inr = (unvested_usd * _USD_TO_INR).quantize(Decimal("1"))
    cost_of_replacement = (current_base * _rnd(rng, 1.5, 2.5)).quantize(Decimal("1"))

    # Pay policy targets
    pay_policy_pctile = _rnd(rng, 45, 65, "0.1")
    policy_target_cr = _rnd(rng, 0.90, 1.05, "0.0001")
    target_cr_val = min(
        Decimal("1.20"),
        max(Decimal("0.80"), policy_target_cr + _rnd(rng, -0.04, 0.04, "0.0001")),
    )
    target_tcc = (target_cr_val * p50 * (Decimal("1") + _BONUS_PCT)).quantize(Decimal("1"))
    new_cr = (rec_base / p50).quantize(Decimal("0.0001")) if p50 else Decimal("1.0000")
    rem_gap = ((policy_target_cr - new_cr) * Decimal("100")).quantize(Decimal("0.01"))

    rationale = _build_rationale_text(
        subject_name=full_name,
        job_title=job_title,
        current_base=current_base,
        rec_base=rec_base,
        criticality=snap.criticality or JvreCriticality.LOW_RISK.value,
        market_position=snap.market_position or JvreMarketPosition.MARKET_ALIGNED.value,
        promotion_readiness=snap.promotion_readiness or JvrePromotionReadiness.NOT_READY.value,
        jvre_score=jvre_score,
    )
    return IquestEngineOutput(
        tenant_id=tenant.id,
        cycle_id=cycle.id,
        subject_user_id=subject.user.id,
        employee_id=f"EMP{(idx + 1):05d}",
        employee_name=full_name,
        department=dept_name,
        bu=dept_name,
        job_family=dept_name,
        job_role=job_title,
        band=subject.level,
        designation=job_title,
        location="India",
        supervisor=supervisor_name,
        doj=doj,
        tenure_years=tenure_yrs,
        gender=rng.choice(["Male", "Female", "Non-binary"]),
        rating_band=subject.level,
        potential_rating=_rnd(rng, 1, 5, "0.1"),
        perf_cycle=cycle.fy_label,
        manager_criticality_score=_rnd(rng, 1, 10),
        current_base_inr=current_base,
        target_bonus_pct=_BONUS_PCT,
        total_cash_inr=total_cash,
        external_cr=external_cr,
        months_since_last_increase=rng.randint(6, 24),
        cost_of_replacement_inr=cost_of_replacement,
        benchmark_family=dept_name,
        effective_p50=p50,
        benchmark_p25=p25,
        benchmark_p50=p50,
        benchmark_p75=p75,
        benchmark_var_pct=_BONUS_PCT,
        ttf_months=_rnd(rng, 2, 8, "0.1"),
        open_hc=rng.randint(0, 5),
        macro_score=_rnd(rng, 3, 9),
        f1_macro_factor=_rnd(rng, 0.5, 2),
        f2_compa_factor=_rnd(rng, 0.5, 2),
        cr_gap_score=_rnd(rng, 1, 10),
        ttf_score=_rnd(rng, 1, 10),
        hc_score=_rnd(rng, 1, 10),
        hp_score=_rnd(rng, 3, 9),
        exit_risk_score=_rnd(rng, 1, 10),
        criticality_score=_rnd(rng, 1, 10),
        f3_crit_factor=_rnd(rng, 0.5, 2),
        unvested_usd=unvested_usd,
        equity_value_inr=equity_inr,
        next_vest_date=next_vest,
        months_to_next_vest=months_to_vest,
        perf_signal=_rnd(rng, 0.5, 1.5),
        exit_risk_signal=_rnd(rng, 0.5, 1.5),
        inc_lag_signal=_rnd(rng, 0.5, 1.5),
        tenure_signal=_rnd(rng, 0.5, 1.5),
        f4_perf_factor=_rnd(rng, 0.5, 2),
        jvre_score=jvre_score,
        jvre_tier=_jvre_tier(jvre_score),
        pay_policy_pctile=pay_policy_pctile,
        policy_target_cr=policy_target_cr,
        target_cr=target_cr_val,
        target_tcc_inr=target_tcc,
        rec_new_base_inr=rec_base,
        rec_increase_pct=rec_increase,
        new_cr_after_rec=new_cr,
        rec_var_pct=_BONUS_PCT,
        rec_total_cash_inr=rec_total_cash,
        scale_factor=Decimal("1.00"),
        capped_rec_increase_pct=rec_increase,
        capped_new_base_inr=rec_base,
        capped_var_pct=_BONUS_PCT,
        capped_total_cash_inr=rec_total_cash,
        rem_gap_to_policy_pctile=rem_gap,
        promotion_flag=promotion_flag,
        multi_cycle_flag=False,
        multi_cycle_plan_flag=False,
        funding_gap_flag=False,
        band_ceiling_flag=False,
        var_pay_alignment_flag=False,
        band_c_review_flag=False,
        rationale=rationale,
    )


async def _create_iquest_engine_output(
    db: AsyncSession,
    *,
    tenant: Tenant,
    cycle: CompensationCycle,
    org: OrgTree,
    rng: random.Random,
) -> None:
    """Seed iquest_engine_output from existing snapshot + benchmark data."""
    from sqlalchemy import select as sa_select

    snapshots_result = await db.execute(
        sa_select(JvreSnapshot).where(
            JvreSnapshot.tenant_id == tenant.id,
            JvreSnapshot.cycle_id == cycle.id,
        )
    )
    snap_by_subject = {s.subject_user_id: s for s in snapshots_result.scalars().all()}

    benchmarks_result = await db.execute(
        sa_select(MarketBenchmark).where(MarketBenchmark.tenant_id == tenant.id)
    )
    bench_by_subject = {b.subject_user_id: b for b in benchmarks_result.scalars().all()}

    def _full_name(u: User) -> str:
        return f"{u.first_name} {u.last_name or ''}".strip()

    supervisor_map: dict = {}
    cfo_name = _full_name(org.cfo.user)
    for mom in org.moms:
        supervisor_map[mom.user.id] = cfo_name
    for mom_idx, mops in enumerate(org.mops_by_mom):
        mom_name = _full_name(org.moms[mom_idx].user)
        for mop in mops:
            supervisor_map[mop.user.id] = mom_name
    for mom_idx, per_mom in enumerate(org.ics_by_mop):
        for mop_idx, ics in enumerate(per_mom):
            mop_name = _full_name(org.mops_by_mom[mom_idx][mop_idx].user)
            for ic in ics:
                supervisor_map[ic.user.id] = mop_name

    for idx, subject in enumerate(org.all_subjects):
        snap = snap_by_subject.get(subject.user.id)
        if snap is None:
            continue
        db.add(_build_engine_row(
            idx=idx,
            subject=subject,
            snap=snap,
            bench=bench_by_subject.get(subject.user.id),
            cycle=cycle,
            tenant=tenant,
            rng=rng,
            supervisor_name=supervisor_map.get(subject.user.id),
        ))
    await db.flush()


async def _create_budget_allocations(
    db: AsyncSession,
    *,
    tenant: Tenant,
    cycle: CompensationCycle,
    org: OrgTree,
    rng: random.Random,
) -> tuple[BudgetAllocation, list[BudgetAllocation]]:
    """Seed the CFO root + per-MoM children.

    The CFO row is SUBMITTED — i.e. the demo starts with the budget
    already pushed down to the MoM tier. Each MoM gets a PENDING
    allocation row whose ``total_pool`` matches the line the CFO's
    submission produced.
    """

    # Per-MoM pool sizes. Compute bottom-up: sum of next-FY pay totals
    # for each MoM's downstream tree, plus a comfortable cushion.
    mom_pools: list[Decimal] = []
    mom_jvre_recs: list[Decimal] = []
    for mom_idx, mom in enumerate(org.moms):
        team_total = mom.pay.total_rewards
        for mop in org.mops_by_mom[mom_idx]:
            team_total += mop.pay.total_rewards
        for mop_idx in range(len(org.mops_by_mom[mom_idx])):
            for ic in org.ics_by_mop[mom_idx][mop_idx]:
                team_total += ic.pay.total_rewards
        # JVRE-recommended pool = team total * ~1.10 (a 10% growth
        # envelope). MoM's actual pool is JVRE * ~1.05 (slight cushion
        # so the MoM has room to allocate above JVRE if they choose).
        jvre_rec = _round_pay(team_total * Decimal("1.10"))
        granted = _round_pay(jvre_rec * Decimal("1.05"))
        mom_pools.append(granted)
        mom_jvre_recs.append(jvre_rec)

    # CFO root allocation -------------------------------------------------
    cfo_total_pool = sum(mom_pools, Decimal("0"))
    cfo_reserve = _round_pay(cfo_total_pool * Decimal("0.05"))
    cfo_alloc = BudgetAllocation(
        tenant_id=tenant.id,
        cycle_id=cycle.id,
        owner_user_id=org.cfo.user.id,
        parent_allocation_id=None,
        total_pool=cfo_total_pool,
        strategic_reserve=cfo_reserve,
        budget_for_allocation=cfo_total_pool - cfo_reserve,
        currency_code=DEFAULT_CURRENCY,
        status=BudgetAllocationStatus.SUBMITTED.value,
        submitted_at=cycle.created_at,
        submitted_by_user_id=org.cfo.user.id,
    )
    db.add(cfo_alloc)
    await db.flush()

    # CFO lines (one per MoM) --------------------------------------------
    cfo_lines: list[BudgetAllocationLine] = []
    for mom_idx, mom in enumerate(org.moms):
        pool = mom_pools[mom_idx]
        jvre_rec = mom_jvre_recs[mom_idx]
        # Split the MoM's pool across the four sub-pools using a
        # plausible ratio (most into base, some variable, some LTI,
        # small reserve).
        base_share = _round_pay(pool * Decimal("0.65"))
        variable_share = _round_pay(pool * Decimal("0.20"))
        lti_share = _round_pay(pool * Decimal("0.10"))
        reserve_share = pool - base_share - variable_share - lti_share
        line = BudgetAllocationLine(
            allocation_id=cfo_alloc.id,
            recipient_user_id=mom.user.id,
            allocated_amount=pool,
            base_pool=base_share,
            variable_pool=variable_share,
            lti_grant_fmv_pool=lti_share,
            reserve_pool=reserve_share,
            jvre_rec_amount=jvre_rec,
            currency_code=DEFAULT_CURRENCY,
            notes=None,
        )
        cfo_lines.append(line)
        db.add(line)
    await db.flush()
    _ = cfo_lines  # held for FK ordering, no further use here
    _ = rng  # keep parameter for symmetry; consumers may add jitter later

    # Per-MoM PENDING allocations ----------------------------------------
    # Pre-seed strategic_reserve at the JVRE midpoint for the MoM band
    # (10-13%, midpoint 11.5%) so the demo doesn't start from zero.
    _MOM_RESERVE_MIDPOINT = Decimal("0.115")
    mom_allocations: list[BudgetAllocation] = []
    for mom_idx, mom in enumerate(org.moms):
        pool = mom_pools[mom_idx]
        mom_reserve = _round_pay(pool * _MOM_RESERVE_MIDPOINT)
        alloc = BudgetAllocation(
            tenant_id=tenant.id,
            cycle_id=cycle.id,
            owner_user_id=mom.user.id,
            parent_allocation_id=cfo_alloc.id,
            total_pool=pool,
            strategic_reserve=mom_reserve,
            budget_for_allocation=pool - mom_reserve,
            currency_code=DEFAULT_CURRENCY,
            status=BudgetAllocationStatus.PENDING.value,
        )
        db.add(alloc)
        mom_allocations.append(alloc)
    await db.flush()

    return cfo_alloc, mom_allocations


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------
async def seed(db: AsyncSession, *, rng_seed: int = 42) -> dict[str, int]:
    """Drop + recreate the demo tenant. Returns counts for the summary."""
    # Bypass RLS for the seed work — we're writing across many tenant-
    # scoped tables before any tenant context exists for the new tenant.
    await db.execute(text("SET app.platform_override = 'true'"))

    await _drop_tenant_if_exists(db)

    rng = random.Random(rng_seed)

    tenant = Tenant(
        code=TENANT_CODE,
        name=TENANT_NAME,
        domain=TENANT_DOMAIN,
        status="ACTIVE",
        default_currency_code=DEFAULT_CURRENCY,
    )
    db.add(tenant)
    await db.flush()

    # bcrypt is intentionally slow. One hash, reused across every demo
    # user — same trick as scripts/seed_loadtest.py. Demo only.
    shared_hash = hash_password(DEMO_PASSWORD)

    org = await _build_users(
        db, tenant=tenant, rng=rng, shared_password_hash=shared_hash
    )
    cycle = await _create_cycle(db, tenant=tenant, cfo=org.cfo)
    await _create_reporting_relationships(
        db, tenant=tenant, cycle=cycle, org=org
    )
    await _create_compensation_history(db, tenant=tenant, org=org, rng=rng)
    await _create_market_benchmarks(db, tenant=tenant, org=org, rng=rng)
    await _create_jvre_snapshots(
        db, tenant=tenant, cycle=cycle, org=org, rng=rng
    )
    await _create_jvre_rationale(db, tenant=tenant, cycle=cycle, org=org)
    await _create_iquest_engine_output(db, tenant=tenant, cycle=cycle, org=org, rng=rng)
    cfo_alloc, mom_allocs = await _create_budget_allocations(
        db, tenant=tenant, cycle=cycle, org=org, rng=rng
    )

    await db.commit()

    return {
        "users": 3 + len(org.moms) + sum(len(m) for m in org.mops_by_mom)
        + len(org.all_ics),
        "moms": len(org.moms),
        "mops": sum(len(m) for m in org.mops_by_mom),
        "ics": len(org.all_ics),
        "reporting_relationships": (
            sum(len(m) for m in org.mops_by_mom)
            + len(org.all_ics)
        ),
        "jvre_rationale": len(org.all_subjects),
        "iquest_engine_output": len(org.all_subjects),
        "jvre_snapshots": len(org.all_subjects),
        "market_benchmarks": len(org.all_subjects),
        "compensation_history": (len(org.all_subjects) + 3) * len(CYCLE_PRIOR_FYS),
        "cfo_root_allocation_pool": int(cfo_alloc.total_pool),
        "mom_pending_allocations": len(mom_allocs),
    }


async def _main() -> int:
    async with AsyncSessionLocal() as db:
        try:
            counts = await seed(db)
        except Exception as exc:
            await db.rollback()
            print(f"ERROR: demo seed failed: {exc}")
            return 1

    print(f"Seeded demo tenant {TENANT_CODE!r} ({TENANT_DOMAIN}).")
    print(f"  users seeded         : {counts['users']}")
    print( "     CFO + CHRO + C&B   :  3")
    print(f"     MoMs               :  {counts['moms']}")
    print(f"     MoPs               :  {counts['mops']}")
    print(f"     ICs                :  {counts['ics']}")
    print(f"  reporting edges      : {counts['reporting_relationships']}")
    print(f"  JVRE snapshots       : {counts['jvre_snapshots']}")
    print(f"  market benchmarks    : {counts['market_benchmarks']}")
    print(f"  comp-history rows    : {counts['compensation_history']}")
    print(
        f"  CFO root pool        : ${counts['cfo_root_allocation_pool']:,} "
        f"(SUBMITTED, with {counts['mom_pending_allocations']} child PENDING allocations)"
    )
    print(f"  password (every user): {DEMO_PASSWORD}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed the oscorp demo tenant for the JVRE workspace."
    )
    parser.parse_args(argv)
    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
