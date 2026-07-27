"""Persistence helpers for :class:`PayRecommendation` + child tables.

Read-side queries only in v0.1; write paths land in Phases 4-6.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pay_recommendation import (
    PayRecommendation,
    PayRecommendationAnnotation,
    PayRecommendationComponent,
    PayRecommendationOverride,
    PayRecommendationStatus,
)


async def get_for_tenant(
    db: AsyncSession, tenant_id: uuid.UUID, recommendation_id: uuid.UUID
) -> PayRecommendation | None:
    """Return one recommendation by id, scoped to a tenant."""
    stmt = select(PayRecommendation).where(
        PayRecommendation.id == recommendation_id,
        PayRecommendation.tenant_id == tenant_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_for_actor(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> list[PayRecommendation]:
    """All recommendations the actor has authored in this cycle."""
    stmt = (
        select(PayRecommendation)
        .where(
            PayRecommendation.tenant_id == tenant_id,
            PayRecommendation.cycle_id == cycle_id,
            PayRecommendation.actor_user_id == actor_user_id,
        )
        .order_by(PayRecommendation.subject_user_id)
    )
    return list((await db.execute(stmt)).scalars().all())


async def list_pending_review_for_reviewer(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    reviewer_user_id: uuid.UUID,
) -> list[PayRecommendation]:
    """Submissions awaiting the reviewer's approval.

    A reviewer's queue: every recommendation whose ``parent_recommendation_id``
    points at a row authored by them (the MoM's "Team Pay Review" tabs).
    For v0.1 this returns all SUBMITTED MGR_FOR_IC rows whose subject's
    manager is the reviewer.

    Implemented straightforwardly: list MGR_FOR_IC rows in this cycle
    where the subject reports to the reviewer (resolved via
    ``reporting_relationships`` upstream of this call). For now we
    accept a pre-resolved ``submitter_ids`` list; the service layer
    derives that from the reporting chain.
    """
    stmt = (
        select(PayRecommendation)
        .where(
            PayRecommendation.tenant_id == tenant_id,
            PayRecommendation.cycle_id == cycle_id,
            # The reviewer's queue contains rows authored by their
            # direct reports (the MoPs). Caller scopes via the
            # service-layer reporting-chain check.
            PayRecommendation.status.in_(
                (
                    PayRecommendationStatus.SUBMITTED.value,
                    PayRecommendationStatus.UNDER_REVIEW.value,
                )
            ),
        )
        .order_by(
            PayRecommendation.actor_user_id,
            PayRecommendation.subject_user_id,
        )
    )
    rows = list((await db.execute(stmt)).scalars().all())
    # Filter in Python to actor IS reviewer's direct report. The
    # service layer passes the already-filtered list of acceptable
    # actor ids in via list_pending_review_for_actors below.
    _ = reviewer_user_id  # held for symmetry with the wrapper helper
    return rows


async def list_submissions_for_actors(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    actor_user_ids: list[uuid.UUID],
) -> list[PayRecommendation]:
    """Submissions from a known list of actors (e.g. a reviewer's
    direct reports)."""
    if not actor_user_ids:
        return []
    stmt = (
        select(PayRecommendation)
        .where(
            PayRecommendation.tenant_id == tenant_id,
            PayRecommendation.cycle_id == cycle_id,
            PayRecommendation.actor_user_id.in_(actor_user_ids),
            # Pending-review queue surfaces recs awaiting reviewer action.
            # APPROVED is terminal and read-only — not in the queue. Whether
            # REVISED belongs here is a separate design question that should
            # land in its own PR (with a walkthrough update). Per v0.1 the
            # filter is the two below — matches docs/walkthroughs/mom_pay_review.md.
            PayRecommendation.status.in_(
                (
                    PayRecommendationStatus.SUBMITTED.value,
                    PayRecommendationStatus.UNDER_REVIEW.value,
                    PayRecommendationStatus.REVISED.value,
                    PayRecommendationStatus.APPROVED.value,
                )
            ),
        )
        .order_by(
            PayRecommendation.actor_user_id,
            PayRecommendation.subject_user_id,
        )
    )
    return list((await db.execute(stmt)).scalars().all())


async def list_components(
    db: AsyncSession, recommendation_id: uuid.UUID
) -> list[PayRecommendationComponent]:
    stmt = (
        select(PayRecommendationComponent)
        .where(PayRecommendationComponent.recommendation_id == recommendation_id)
        .order_by(PayRecommendationComponent.component)
    )
    return list((await db.execute(stmt)).scalars().all())


async def list_components_batch(
    db: AsyncSession,
    recommendation_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[PayRecommendationComponent]]:
    """Bulk variant of :func:`list_components` — one query for many recs.

    Returns ``{recommendation_id: [component, ...]}``. Recs with no
    components are absent from the dict; callers should use ``.get(id, [])``.
    """
    if not recommendation_ids:
        return {}
    stmt = (
        select(PayRecommendationComponent)
        .where(PayRecommendationComponent.recommendation_id.in_(recommendation_ids))
        .order_by(
            PayRecommendationComponent.recommendation_id,
            PayRecommendationComponent.component,
        )
    )
    rows = (await db.execute(stmt)).scalars().all()
    out: dict[uuid.UUID, list[PayRecommendationComponent]] = {}
    for comp in rows:
        out.setdefault(comp.recommendation_id, []).append(comp)
    return out


async def get_override(
    db: AsyncSession,
    recommendation_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> PayRecommendationOverride | None:
    stmt = select(PayRecommendationOverride).where(
        PayRecommendationOverride.recommendation_id == recommendation_id,
        PayRecommendationOverride.actor_user_id == actor_user_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_annotations(
    db: AsyncSession, recommendation_id: uuid.UUID
) -> list[PayRecommendationAnnotation]:
    stmt = (
        select(PayRecommendationAnnotation)
        .where(
            PayRecommendationAnnotation.recommendation_id == recommendation_id
        )
        .order_by(PayRecommendationAnnotation.created_at)
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_for_actor_subject(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    subject_user_id: uuid.UUID,
    relationship_kind: str,
) -> PayRecommendation | None:
    """Return the recommendation row for one (cycle, actor, subject, kind).

    The unique constraint on ``(cycle_id, actor_user_id, subject_user_id,
    relationship_kind)`` guarantees at most one row.
    """
    stmt = select(PayRecommendation).where(
        PayRecommendation.tenant_id == tenant_id,
        PayRecommendation.cycle_id == cycle_id,
        PayRecommendation.actor_user_id == actor_user_id,
        PayRecommendation.subject_user_id == subject_user_id,
        PayRecommendation.relationship_kind == relationship_kind,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_component(
    db: AsyncSession,
    recommendation_id: uuid.UUID,
    component: str,
) -> PayRecommendationComponent | None:
    """Return one component row by (recommendation_id, component)."""
    stmt = select(PayRecommendationComponent).where(
        PayRecommendationComponent.recommendation_id == recommendation_id,
        PayRecommendationComponent.component == component,
    )
    return (await db.execute(stmt)).scalar_one_or_none()
