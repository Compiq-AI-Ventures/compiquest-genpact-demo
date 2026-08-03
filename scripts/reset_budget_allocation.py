"""Reset submitted budget allocations and/or pay recommendations for re-testing.

Two separate submit actions get locked during a demo/test run, and this
script undoes either or both:

**Budget allocation** (``POST /budget-allocations/{id}/submit``):
* ``status`` -> ``SUBMITTED``, ``submitted_at``/``submitted_by_user_id`` set,
  lines locked.
* Reset: flip back to ``PENDING``, clear those two fields.

**Pay recommendation** (``POST /pay-recommendations/submit``, plus the
reviewer's approve/revise actions):
* ``status`` -> ``SUBMITTED`` (actor) or ``APPROVED``/``REVISED``
  (reviewer), ``submitted_at``/``approved_at``/``saved_at`` set.
* Reset: flip back to ``DRAFT``, clear those three fields — this covers a
  recommendation regardless of which side (actor or reviewer) last touched
  it, so both the submit and the review step can be re-tested from scratch.

Neither reset touches the underlying numbers (``budget_allocation_lines``,
``pay_recommendation_components``) — only the status/timestamp fields that
gate editability. Cascaded child *budget* allocations are left alone by
default (see ``--delete-cascaded-children``); pay recommendations have no
such cascade — the reviewer's row already exists pre-seeded, it's just a
second ``PayRecommendation`` row pointing at the actor's via
``parent_recommendation_id``, so resetting both independently is enough.

Usage:
    # Reset everything (budget + pay-rec) for one person, active cycle
    uv run python -m scripts.reset_budget_allocation --email sarah.smith.pnlhead@genpact.com

    # Just one or the other
    uv run python -m scripts.reset_budget_allocation --email <email> --type budget
    uv run python -m scripts.reset_budget_allocation --email <email> --type pay-rec

    # Reset every submitted budget allocation and/or pay recommendation in the active cycle
    uv run python -m scripts.reset_budget_allocation --all
    uv run python -m scripts.reset_budget_allocation --all --type pay-rec

    # Also blow away cascade-created budget-allocation children (destructive)
    uv run python -m scripts.reset_budget_allocation --email <email> --delete-cascaded-children
"""

from __future__ import annotations

import argparse
import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

load_dotenv()

from app.models.budget_allocation import BudgetAllocation, BudgetAllocationStatus
from app.models.compensation_cycle import CompensationCycle, CompensationCycleStatus
from app.models.pay_recommendation import PayRecommendation, PayRecommendationStatus
from app.models.user import User

# How close a child's created_at has to be to the parent's submitted_at to be
# treated as "created by this submission" rather than pre-existing (e.g. from
# the seed script). The cascade creates children synchronously in the same
# request, so this is generous on purpose.
_CASCADE_WINDOW_SECONDS = 5

_NON_DRAFT_REC_STATUSES = (
    PayRecommendationStatus.SUBMITTED.value,
    PayRecommendationStatus.UNDER_REVIEW.value,
    PayRecommendationStatus.APPROVED.value,
    PayRecommendationStatus.REVISED.value,
)


async def _reset_budget_allocation(
    db: AsyncSession, alloc: BudgetAllocation, *, delete_cascaded_children: bool
) -> None:
    owner = await db.get(User, alloc.owner_user_id)
    label = owner.email if owner else str(alloc.owner_user_id)

    if alloc.status != BudgetAllocationStatus.SUBMITTED.value:
        print(f"  skip {label} (budget): status is {alloc.status}, not SUBMITTED")
        return

    submitted_at = alloc.submitted_at

    if delete_cascaded_children and submitted_at is not None:
        children = (
            (
                await db.execute(
                    select(BudgetAllocation).where(
                        BudgetAllocation.parent_allocation_id == alloc.id
                    )
                )
            )
            .scalars()
            .all()
        )
        cascaded = [
            c
            for c in children
            if abs((c.created_at - submitted_at).total_seconds()) <= _CASCADE_WINDOW_SECONDS
        ]
        for child in cascaded:
            child_owner = await db.get(User, child.owner_user_id)
            child_label = child_owner.email if child_owner else str(child.owner_user_id)
            print(f"    deleting cascaded child allocation for {child_label}")
            await db.delete(child)

    alloc.status = BudgetAllocationStatus.PENDING.value
    alloc.submitted_at = None
    alloc.submitted_by_user_id = None
    print(f"  reset {label} (budget): SUBMITTED -> PENDING")


async def _reset_pay_recommendation(db: AsyncSession, rec: PayRecommendation) -> None:
    actor = await db.get(User, rec.actor_user_id)
    subject = await db.get(User, rec.subject_user_id)
    actor_label = actor.email if actor else str(rec.actor_user_id)
    subject_label = subject.email if subject else str(rec.subject_user_id)

    if rec.status == PayRecommendationStatus.DRAFT.value:
        print(f"  skip {actor_label} -> {subject_label} (pay-rec): already DRAFT")
        return

    old_status = rec.status
    rec.status = PayRecommendationStatus.DRAFT.value
    rec.submitted_at = None
    rec.approved_at = None
    rec.saved_at = None
    print(f"  reset {actor_label} -> {subject_label} (pay-rec): {old_status} -> DRAFT")


async def _reset_budgets_for_owner(db: AsyncSession, user: User, cycle_id, *, delete_cascaded_children: bool) -> None:
    alloc = (
        await db.execute(
            select(BudgetAllocation).where(
                BudgetAllocation.cycle_id == cycle_id,
                BudgetAllocation.owner_user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if alloc is None:
        print(f"  no budget allocation found for {user.email} in the active cycle")
        return
    await _reset_budget_allocation(db, alloc, delete_cascaded_children=delete_cascaded_children)


async def _reset_pay_recs_for_actor(db: AsyncSession, user: User, cycle_id) -> None:
    recs = (
        (
            await db.execute(
                select(PayRecommendation).where(
                    PayRecommendation.cycle_id == cycle_id,
                    PayRecommendation.actor_user_id == user.id,
                )
            )
        )
        .scalars()
        .all()
    )
    if not recs:
        print(f"  no pay recommendations found for {user.email} in the active cycle")
        return
    for rec in recs:
        await _reset_pay_recommendation(db, rec)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--email", help="Actor/owner email to reset")
    target.add_argument(
        "--all", action="store_true", help="Reset every submitted row in the active cycle"
    )
    parser.add_argument(
        "--type",
        choices=["budget", "pay-rec", "both"],
        default="both",
        help="Which kind of submission to reset (default: both)",
    )
    parser.add_argument(
        "--delete-cascaded-children",
        action="store_true",
        help="Also delete budget-allocation child rows created by this submission (destructive)",
    )
    args = parser.parse_args()

    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with AsyncSession(engine) as db:
        cycle = (
            await db.execute(
                select(CompensationCycle).where(
                    CompensationCycle.status == CompensationCycleStatus.ACTIVE.value
                )
            )
        ).scalar_one_or_none()
        if cycle is None:
            print("No ACTIVE compensation cycle found.")
            return

        reset_budget = args.type in ("budget", "both")
        reset_pay_rec = args.type in ("pay-rec", "both")

        if args.all:
            if reset_budget:
                allocs = (
                    (
                        await db.execute(
                            select(BudgetAllocation).where(
                                BudgetAllocation.cycle_id == cycle.id,
                                BudgetAllocation.status == BudgetAllocationStatus.SUBMITTED.value,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                print(f"Resetting {len(allocs)} submitted budget allocation(s) …")
                for alloc in allocs:
                    await _reset_budget_allocation(
                        db, alloc, delete_cascaded_children=args.delete_cascaded_children
                    )

            if reset_pay_rec:
                recs = (
                    (
                        await db.execute(
                            select(PayRecommendation).where(
                                PayRecommendation.cycle_id == cycle.id,
                                PayRecommendation.status.in_(_NON_DRAFT_REC_STATUSES),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                print(f"Resetting {len(recs)} non-draft pay recommendation(s) …")
                for rec in recs:
                    await _reset_pay_recommendation(db, rec)
        else:
            user = (
                await db.execute(select(User).where(User.email == args.email))
            ).scalar_one_or_none()
            if user is None:
                print(f"No user found with email {args.email!r}.")
                return

            if reset_budget:
                await _reset_budgets_for_owner(
                    db, user, cycle.id, delete_cascaded_children=args.delete_cascaded_children
                )
            if reset_pay_rec:
                await _reset_pay_recs_for_actor(db, user, cycle.id)

        await db.commit()
        print("Done.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
