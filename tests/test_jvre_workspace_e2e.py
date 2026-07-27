"""End-to-end test for the JVRE workspace v0.1.

One scenario, walked through HTTP, asserting on each phase boundary
plus the final lineage and audit trail. Per spec section 11 / Phase 8:

    MoM allocates → MoP recommends → MoM reviews → MoM approves

The seeded org tree is small (CFO + 1 MoM + 1 MoP + 2 ICs) so the
test stays under a few seconds — we still pay one bcrypt-verify per
login (3 logins total: MoM, MoP, MoM-as-reviewer-reuses-token).

What this test guarantees
-------------------------
* The Phase 4 → Phase 5 cascade actually fires (MoM submit creates
  the MoP's PENDING allocation; the MoP can immediately use it).
* The Phase 5 → Phase 6 hand-off works (MoP submission is visible in
  the MoM's pending-review queue).
* Per-component lineage is preserved end-to-end (mgr_rec_value from
  MoP, mom_rec_value from MoM, final_value computed correctly).
* Every state transition writes its audit row.
* Annotations attached during revise survive the approval step.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.models.audit_log import AuditLog
from app.models.budget_allocation import BudgetAllocation
from app.models.pay_recommendation import (
    PayRecommendation,
    PayRecommendationAnnotation,
    PayRecommendationComponent,
)
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests._jvre_workspace_helpers import E2E_PASSWORD, seed_minimal_jvre_tree


async def _login(client: AsyncClient, email: str) -> dict[str, str]:
    """Log in and return the Authorization header dict."""
    response = await client.post(
        "/auth/login",
        json={"email": email, "password": E2E_PASSWORD},
    )
    assert response.status_code == 200, response.text
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _expect_ok(response, *, expected: int = 200) -> dict:
    """Assert status code + return parsed JSON. Single-line guard so
    the test reads as a sequence of HTTP intentions."""
    assert response.status_code == expected, (
        f"expected {expected}, got {response.status_code}: {response.text}"
    )
    return response.json()


async def test_full_jvre_workspace_walk(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """MoM allocates → MoP recommends → MoM reviews → MoM approves.

    Single end-to-end scenario. Asserts on the final `final_value`
    lineage per component, the audit trail, and the annotation feed.
    """
    tree = await seed_minimal_jvre_tree(db_session)
    cycle_id = tree.cycle.id
    mom_alloc_id = tree.mom_alloc.id

    # =====================================================================
    # Phase 4 — MoM allocates
    # =====================================================================
    mom_headers = await _login(client, tree.mom.email)

    # Active cycle resolves correctly.
    cycle_body = await _expect_ok(
        await client.get("/comp-cycles/active", headers=mom_headers)
    )
    assert cycle_body["data"]["id"] == str(cycle_id)
    assert cycle_body["data"]["status"] == "ACTIVE"

    # MoM's allocation is PENDING with reserve = 0 from the seed.
    alloc_body = await _expect_ok(
        await client.get(
            f"/comp-cycles/{cycle_id}/my-budget-allocation",
            headers=mom_headers,
        )
    )
    assert alloc_body["data"]["status"] == "PENDING"
    assert Decimal(alloc_body["data"]["strategic_reserve"]) == Decimal("0")

    # No lines yet — list returns empty.
    lines_body = await _expect_ok(
        await client.get(
            f"/budget-allocations/{mom_alloc_id}/lines",
            headers=mom_headers,
        )
    )
    assert lines_body["data"]["total"] == 0

    # Align-with-jvre materializes one line for the MoP.
    aligned = await _expect_ok(
        await client.post(
            f"/budget-allocations/{mom_alloc_id}/align-with-jvre",
            headers=mom_headers,
        )
    )
    assert aligned["data"]["total"] == 1
    line = aligned["data"]["items"][0]
    assert line["recipient_user_id"] == str(tree.mop.id)
    # JVRE rec = sum(MoP + IC1 + IC2 leaves) = 200000 + 104000 + 91000.
    assert Decimal(line["jvre_rec_amount"]) == Decimal("395000")
    # Allocated defaults to JVRE rec.
    assert Decimal(line["allocated_amount"]) == Decimal("395000")

    # Submit — flips status, cascades the MoP allocation in PENDING,
    # writes BUDGET_SUBMITTED audit row.
    submitted = await _expect_ok(
        await client.post(
            f"/budget-allocations/{mom_alloc_id}/submit",
            headers=mom_headers,
        )
    )
    assert submitted["data"]["status"] == "SUBMITTED"
    assert submitted["data"]["submitted_at"] is not None

    # Audit row landed.
    audit_rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "BUDGET_SUBMITTED",
                AuditLog.resource_id == str(mom_alloc_id),
            )
        )
    ).scalars().all()
    assert len(audit_rows) == 1
    assert audit_rows[0].extra_data["lines_count"] == 1
    assert audit_rows[0].extra_data["child_allocations_created"] == 1

    # MoP child allocation now exists in PENDING.
    mop_alloc = (
        await db_session.execute(
            select(BudgetAllocation).where(
                BudgetAllocation.cycle_id == cycle_id,
                BudgetAllocation.owner_user_id == tree.mop.id,
            )
        )
    ).scalar_one()
    assert mop_alloc.status == "PENDING"
    assert mop_alloc.total_pool == Decimal("395000")

    # =====================================================================
    # Phase 5 — MoP recommends
    # =====================================================================
    mop_headers = await _login(client, tree.mop.email)

    # MoP's "my-recommendations" lists 2 ICs, no rec rows yet.
    my_recs_body = await _expect_ok(
        await client.get(
            f"/comp-cycles/{cycle_id}/my-recommendations",
            headers=mop_headers,
        )
    )
    assert my_recs_body["data"]["total"] == 2
    for item in my_recs_body["data"]["items"]:
        assert item["recommendation_id"] is None
        assert item["status"] == "PENDING"

    subject_ids = sorted(
        item["subject_user_id"] for item in my_recs_body["data"]["items"]
    )
    assert sorted(str(uid) for uid in (tree.ic1.id, tree.ic2.id)) == subject_ids

    # Open + save a recommendation per IC. The first one (ic1) gets an
    # explicit override on BASE_PAY with full metadata so we can
    # assert on the override-row lineage at the end.
    ic1_rec_id: uuid.UUID | None = None
    for subject_id in subject_ids:
        created = await _expect_ok(
            await client.post(
                f"/comp-cycles/{cycle_id}/recommendations",
                json={"subject_user_id": subject_id},
                headers=mop_headers,
            ),
            expected=201,
        )
        rec = created["data"]
        assert rec["status"] == "DRAFT"
        # Five components seeded from JVRE.
        assert len(rec["components"]) == 5
        for comp in rec["components"]:
            # mgr_rec_value defaulted to JVRE on creation.
            assert comp["mgr_rec_value"] == comp["jvre_rec_value"]
            assert comp["mom_rec_value"] is None

        if subject_id == str(tree.ic1.id):
            ic1_rec_id = uuid.UUID(rec["id"])
            base = next(
                c for c in rec["components"] if c["component"] == "BASE_PAY"
            )
            override_value = (Decimal(base["jvre_rec_value"]) * Decimal("0.95"))
            await _expect_ok(
                await client.put(
                    f"/pay-recommendations/{rec['id']}/components/BASE_PAY",
                    json={
                        "value": str(override_value),
                        "reason_code": "EXTERNAL_OFFER_COUNTERED",
                        "role_criticality": "HIGH",
                        "promotion_consideration": True,
                    },
                    headers=mop_headers,
                )
            )

        await _expect_ok(
            await client.post(
                f"/pay-recommendations/{rec['id']}/save",
                headers=mop_headers,
            )
        )

    assert ic1_rec_id is not None

    # Submit the batch.
    submit_body = await _expect_ok(
        await client.post(
            f"/comp-cycles/{cycle_id}/my-recommendations/submit",
            headers=mop_headers,
        )
    )
    assert all(
        item["status"] == "SUBMITTED" for item in submit_body["data"]["items"]
    )

    # Two RECOMMENDATION_SUBMITTED audit rows landed.
    submitted_audits = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "RECOMMENDATION_SUBMITTED",
            )
        )
    ).scalars().all()
    assert len(submitted_audits) == 2

    # =====================================================================
    # Phase 6 — MoM reviews + revises one + approves both
    # =====================================================================
    # Re-login as MoM. (Could re-use Phase 4 token, but reissuing keeps
    # the test resistant to token-expiry edge cases.)
    mom_headers = await _login(client, tree.mom.email)

    queue_body = await _expect_ok(
        await client.get(
            "/pay-recommendations/pending-review", headers=mom_headers
        )
    )
    submitters_with_work = [
        s for s in queue_body["data"]["submitters"] if s["member_count"] > 0
    ]
    assert len(submitters_with_work) == 1
    assert submitters_with_work[0]["submitter_user_id"] == str(tree.mop.id)
    assert submitters_with_work[0]["member_count"] == 2

    # Get the IC1 rec by id (we kept it from Phase 5).
    ic1_rec_body = await _expect_ok(
        await client.get(
            f"/pay-recommendations/{ic1_rec_id}", headers=mom_headers
        )
    )
    ic1_rec = ic1_rec_body["data"]
    assert ic1_rec["status"] == "SUBMITTED"
    base_comp = next(
        c for c in ic1_rec["components"] if c["component"] == "BASE_PAY"
    )
    # MoP override is preserved as mgr_rec_value.
    expected_mgr_value = (Decimal("80000") * Decimal("0.95")).quantize(
        Decimal("0.01")
    )
    assert Decimal(base_comp["mgr_rec_value"]) == expected_mgr_value
    assert base_comp["mom_rec_value"] is None
    # final_value falls back to mgr_rec_value while mom_rec is NULL.
    assert Decimal(base_comp["final_value"]) == expected_mgr_value

    # MoM overrides BASE_PAY back UP — value lands in mom_rec_value,
    # mgr_rec_value untouched, and the implicit SUBMITTED→UNDER_REVIEW
    # status flip fires.
    mom_override_value = Decimal("82000")
    overridden_body = await _expect_ok(
        await client.put(
            f"/pay-recommendations/{ic1_rec_id}/components/BASE_PAY",
            json={
                "value": str(mom_override_value),
                "reason_code": "ROLE_CRITICALITY_REVIEW",
                "role_criticality": "HIGH",
                "promotion_consideration": False,
            },
            headers=mom_headers,
        )
    )
    overridden = overridden_body["data"]
    assert overridden["status"] == "UNDER_REVIEW"
    overridden_base = next(
        c for c in overridden["components"] if c["component"] == "BASE_PAY"
    )
    assert Decimal(overridden_base["mgr_rec_value"]) == expected_mgr_value
    assert Decimal(overridden_base["mom_rec_value"]) == mom_override_value
    assert Decimal(overridden_base["final_value"]) == mom_override_value

    # Revise with an annotation — status flips to REVISED, annotation
    # row materializes under the MoM's name.
    revised_body = await _expect_ok(
        await client.post(
            f"/pay-recommendations/{ic1_rec_id}/revise",
            json={
                "annotation_text": (
                    "MoM action: pay structure adjusted; promotion deferred."
                ),
            },
            headers=mom_headers,
        )
    )
    revised = revised_body["data"]
    assert revised["status"] == "REVISED"
    assert len(revised["annotations"]) == 1
    assert revised["annotations"][0]["actor_user_id"] == str(tree.mom.id)
    assert "promotion deferred" in revised["annotations"][0]["text"]

    # Approve. Status flips to APPROVED + approved_at set.
    approved_body = await _expect_ok(
        await client.post(
            f"/pay-recommendations/{ic1_rec_id}/approve",
            headers=mom_headers,
        )
    )
    approved = approved_body["data"]
    assert approved["status"] == "APPROVED"
    assert approved["approved_at"] is not None
    # Annotation survives.
    assert len(approved["annotations"]) == 1

    # Approve the second IC's rec too (no overrides — straight
    # JVRE-aligned approval).
    other_rec_row = (
        await db_session.execute(
            select(PayRecommendation).where(
                PayRecommendation.subject_user_id == tree.ic2.id,
                PayRecommendation.actor_user_id == tree.mop.id,
            )
        )
    ).scalar_one()
    await _expect_ok(
        await client.post(
            f"/pay-recommendations/{other_rec_row.id}/approve",
            headers=mom_headers,
        )
    )

    # =====================================================================
    # Final state assertions — lineage + audit trail
    # =====================================================================
    # Both recs should be APPROVED with correct lineage.
    final_recs = (
        await db_session.execute(
            select(PayRecommendation).where(
                PayRecommendation.cycle_id == cycle_id
            )
        )
    ).scalars().all()
    assert len(final_recs) == 2
    assert all(r.status == "APPROVED" for r in final_recs)
    assert all(r.approved_at is not None for r in final_recs)

    # IC1's BASE_PAY component has the full lineage chain.
    ic1_base = (
        await db_session.execute(
            select(PayRecommendationComponent).where(
                PayRecommendationComponent.recommendation_id == ic1_rec_id,
                PayRecommendationComponent.component == "BASE_PAY",
            )
        )
    ).scalar_one()
    assert ic1_base.jvre_rec_value == Decimal("80000")
    assert ic1_base.mgr_rec_value == expected_mgr_value
    assert ic1_base.mom_rec_value == mom_override_value

    # IC1's annotation feed has the revise note (1 row).
    annotations = (
        await db_session.execute(
            select(PayRecommendationAnnotation).where(
                PayRecommendationAnnotation.recommendation_id == ic1_rec_id
            )
        )
    ).scalars().all()
    assert len(annotations) == 1
    assert annotations[0].actor_user_id == tree.mom.id

    # Audit trail roll-up.
    audit_summary: dict[str, int] = {}
    for row in (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action.in_(
                    [
                        "BUDGET_SUBMITTED",
                        "RECOMMENDATION_SUBMITTED",
                        "RECOMMENDATION_APPROVED",
                        "RECOMMENDATION_REVISED",
                    ]
                )
            )
        )
    ).scalars().all():
        audit_summary[row.action] = audit_summary.get(row.action, 0) + 1

    assert audit_summary == {
        "BUDGET_SUBMITTED": 1,
        "RECOMMENDATION_SUBMITTED": 2,
        "RECOMMENDATION_REVISED": 1,
        "RECOMMENDATION_APPROVED": 2,
    }
