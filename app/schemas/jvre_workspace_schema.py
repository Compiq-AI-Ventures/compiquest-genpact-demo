"""Pydantic schemas for the JVRE workspace API (read + write).

All wire shapes for the v0.1 endpoints live in this one module so the
contract is easy to scan in one place. Splitting into separate files
would be premature given how much these compose (an allocation line
embeds JVRE chips, a recommendation summary embeds a JVRE snapshot,
etc.).

Naming convention:

* ``…Response``  — top-level shape returned by an endpoint.
* ``…Request``   — top-level shape consumed by a write endpoint.
* bare names      — nested shapes embedded in a parent.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# CompensationCycle
# ---------------------------------------------------------------------------
class CycleResponse(BaseModel):
    """A compensation cycle row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    fy_label: str
    status: str
    submission_deadline: date | None
    currency_code: str
    jvre_alignment_tolerance: float
    cycle_started_at: datetime | None


# ---------------------------------------------------------------------------
# JVRE snapshot (the engine's recommendation for one subject)
# ---------------------------------------------------------------------------
class JvreSnapshotResponse(BaseModel):
    """JVRE recommendation for one (cycle, subject)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cycle_id: uuid.UUID
    subject_user_id: uuid.UUID

    # Subject identity — populated by service layer from users table.
    subject_name: str | None = None
    job_title: str | None = None
    current_level: str | None = None
    compa_ratio: Decimal | None = None

    # Current actuals (stored on snapshot row).
    current_base: Decimal | None = None
    current_variable: Decimal | None = None
    current_tcc: Decimal | None = None
    current_fy_vesting_units: int | None = None

    # JVRE recommendations.
    recommended_base: Decimal | None
    recommended_variable: Decimal | None
    recommended_lti_fmv: Decimal | None
    recommended_lti_units: int | None
    recommended_other_rewards: Decimal | None
    currency_code: str

    # Computed recommendation totals.
    rec_tcc: Decimal | None = None
    rec_increase_pct: Decimal | None = None

    # Classification chips.
    criticality: str | None
    market_position: str | None
    promotion_readiness: str | None
    recommended_level: str | None = None
    risk_callout_text: str | None = None
    market_gap: Decimal | None = None

    # Engine outputs.
    jvre_score: Decimal | None = None
    ai_suggestion_text: str | None

    # Timestamp of when this snapshot was generated.
    generated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Budget allocation — left panel (Budget Planner)
# ---------------------------------------------------------------------------
class JvreReserveRecommendation(BaseModel):
    """JVRE-recommended strategic-reserve range for a Budget Planner.

    For v0.1 the bands are derived heuristically from the owner's tier
    (MoM gets 10-13%, MoP gets 4-8%); when the real JVRE engine lands
    in v0.2 these will come from a snapshot column.
    """

    min_pct: float
    max_pct: float
    midpoint_pct: float
    midpoint_amount: Decimal


class MyBudgetAllocationResponse(BaseModel):
    """Caller's own budget allocation row + JVRE recommendation context."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cycle_id: uuid.UUID
    owner_user_id: uuid.UUID
    parent_allocation_id: uuid.UUID | None
    total_pool: Decimal
    strategic_reserve: Decimal
    budget_for_allocation: Decimal
    currency_code: str
    status: str
    submitted_at: datetime | None
    created_at: datetime | None  # ADD THIS
    parent_owner_name: str | None

    # Computed for the screen.
    current_pool_value: Decimal | None
    pool_delta_vs_current_pct: float | None
    jvre_recommended_pool: Decimal | None
    jvre_engine_recommends_text: str | None
    jvre_reserve: JvreReserveRecommendation | None


# ---------------------------------------------------------------------------
# Budget allocation lines — right panel (per-recipient cards)
# ---------------------------------------------------------------------------
class BudgetAllocationLineResponse(BaseModel):
    """One per-recipient line on a parent allocation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    allocation_id: uuid.UUID
    recipient_user_id: uuid.UUID
    recipient_name: str
    recipient_department: str | None
    recipient_team_size: int | None

    allocated_amount: Decimal
    base_pool: Decimal
    variable_pool: Decimal
    lti_grant_fmv_pool: Decimal
    reserve_pool: Decimal
    jvre_rec_amount: Decimal
    currency_code: str
    notes: str | None

    # Chips from the recipient's JVRE snapshot.
    criticality: str | None
    market_position: str | None
    promotion_readiness: str | None
    compa_ratio: Decimal | None = None
    current_level: str | None = None
    risk_callout_text: str | None = None
    recommended_level: str | None = None
    market_gap: Decimal | None = None
    job_title: str | None = None
    current_pool: Decimal | None = None
    jvre_base_pool: Decimal | None = None
    jvre_variable_pool: Decimal | None = None
    jvre_reserve_pool: Decimal | None = None
    comp_base_pay: Decimal | None = None
    comp_variable_pay: Decimal | None = None
    comp_lti_fmv: Decimal | None = None
    comp_other_rewards: Decimal | None = None
    ai_suggestion_text: str | None = None


# ---------------------------------------------------------------------------
# Pay recommendation
# ---------------------------------------------------------------------------
class PayRecommendationComponentResponse(BaseModel):
    """One component (BASE_PAY, VARIABLE_PAY, …) of a recommendation."""

    model_config = ConfigDict(from_attributes=True)

    component: str
    current_value: Decimal | None
    jvre_rec_value: Decimal | None
    mgr_rec_value: Decimal | None
    mom_rec_value: Decimal | None
    final_value: Decimal | None
    currency_code: str


class PayRecommendationOverrideResponse(BaseModel):
    """Override metadata captured the first time an actor moves off JVRE."""

    model_config = ConfigDict(from_attributes=True)

    actor_user_id: uuid.UUID
    reason_code: str | None
    role_criticality: str | None
    promotion_consideration: bool
    created_at: datetime


class PayRecommendationAnnotationResponse(BaseModel):
    """One narrative annotation on a recommendation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_user_id: uuid.UUID
    actor_name: str
    text: str
    created_at: datetime


class PayRecommendationResponse(BaseModel):
    """Full snapshot of one pay recommendation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cycle_id: uuid.UUID
    actor_user_id: uuid.UUID
    subject_user_id: uuid.UUID
    subject_name: str
    subject_level: str | None
    subject_department: str | None
    parent_recommendation_id: uuid.UUID | None
    relationship_kind: str
    status: str
    submitted_at: datetime | None
    approved_at: datetime | None
    currency_code: str

    components: list[PayRecommendationComponentResponse]
    override: PayRecommendationOverrideResponse | None
    annotations: list[PayRecommendationAnnotationResponse]

    # JVRE chips embedded for the card.
    jvre_snapshot: JvreSnapshotResponse | None


# ---------------------------------------------------------------------------
# /comp-cycles/{id}/my-recommendations — subject row in scope
# ---------------------------------------------------------------------------
class MyRecommendationSubjectResponse(BaseModel):
    """One row of "subjects the caller is responsible for in this cycle".

    Some of these subjects may not yet have a recommendation row (the
    MoP hasn't clicked anything); the ``recommendation`` field is
    ``None`` in that case. The frontend creates the row on first edit
    via the Phase 5 write endpoint.
    """

    subject_user_id: uuid.UUID
    subject_name: str
    subject_level: str | None
    subject_department: str | None
    job_title: str | None = None

    # NULL when the recommendation hasn't been created yet.
    recommendation_id: uuid.UUID | None
    status: str  # PENDING when no rec yet; otherwise the rec status

    # Computed totals so the list can render summary chips without a
    # second round-trip per row.
    final_total_rewards: Decimal | None
    jvre_rec_total: Decimal | None
    deviation_pct: float | None
    mgr_rec_total: Decimal | None
    mom_rec_total: Decimal | None

    # Embedded JVRE chips.
    criticality: str | None
    market_position: str | None
    promotion_readiness: str | None
    compa_ratio: Decimal | None = None
    current_level: str | None = None
    risk_callout_text: str | None = None
    recommended_level: str | None = None
    market_gap: Decimal | None = None
    comp_base_pay: Decimal | None = None
    comp_variable_pay: Decimal | None = None
    comp_lti_fmv: Decimal | None = None
    comp_other_rewards: Decimal | None = None


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
class MarketBenchmarkResponse(BaseModel):
    """Per-subject market-pay reference."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subject_user_id: uuid.UUID
    current_pay: Decimal
    target_pay: Decimal
    currency_code: str
    compa_ratio: Decimal
    target_compa_ratio_min: Decimal | None
    target_compa_ratio_max: Decimal | None
    delta_status_text: str | None


class CompensationHistoryRowResponse(BaseModel):
    """One historical FY row for a subject."""

    model_config = ConfigDict(from_attributes=True)

    fy_label: str
    level_code: str | None
    comp_change_amount: Decimal | None
    currency_code: str
    perf_rating: str | None
    was_promoted: bool


class CompensationHistoryResponse(BaseModel):
    """Wrapper so the endpoint returns a typed envelope."""

    subject_user_id: uuid.UUID
    rows: list[CompensationHistoryRowResponse]


# ---------------------------------------------------------------------------
# /pay-recommendations/pending-review — grouped by submitter (drives tabs)
# ---------------------------------------------------------------------------
class PendingReviewSubmitter(BaseModel):
    """One submitter (a direct-report Manager) and their submission set."""

    submitter_user_id: uuid.UUID
    submitter_name: str
    submitter_department: str | None
    member_count: int

    # Roll-up status: COMPLETED if the caller has approved every row;
    # IN_REVIEW if some are UNDER_REVIEW; otherwise PENDING.
    review_status: str

    # Lightweight per-row summary for the right panel.
    members: list[MyRecommendationSubjectResponse]


class PendingReviewResponse(BaseModel):
    """Top-level envelope for the MoM's "Team Pay Review" page."""

    cycle_id: uuid.UUID
    submitters: list[PendingReviewSubmitter]


# ---------------------------------------------------------------------------
# Write requests — Phase 4 (MoM Budget Allocation)
# ---------------------------------------------------------------------------
class BudgetAllocationUpdateRequest(BaseModel):
    """Patch the strategic_reserve on the caller's allocation.

    Only the strategic_reserve field is mutable in v0.1; ``total_pool``
    is set by the upstream tier (CFO for MoMs, MoM for MoPs) and isn't
    editable by the owner. ``budget_for_allocation`` is recomputed by
    the service as ``total_pool - strategic_reserve``.
    """

    strategic_reserve: Decimal = Field(..., ge=0)


class BudgetAllocationLineUpdateRequest(BaseModel):
    """Patch one recipient's line on a budget allocation.

    Two editing modes are supported:

    * **Quick edit** — only ``allocated_amount`` provided. The four
      sub-pools (base / variable / LTI FMV / reserve) are scaled
      proportionally to keep their current ratio.
    * **Detailed edit** — any of the four sub-pool fields provided.
      ``allocated_amount`` is recomputed as the sum of the four pools
      (whether all four are sent or not — unspecified pools keep their
      current value).
    """

    allocated_amount: Decimal | None = Field(default=None, ge=0)
    base_pool: Decimal | None = Field(default=None, ge=0)
    variable_pool: Decimal | None = Field(default=None, ge=0)
    lti_grant_fmv_pool: Decimal | None = Field(default=None, ge=0)
    reserve_pool: Decimal | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------------------
# Write requests — Phase 5 (MoP / MoM Pay Recommendation)
# ---------------------------------------------------------------------------
class PayRecommendationCreateRequest(BaseModel):
    """Open a draft recommendation for one subject in the active cycle.

    Idempotent — if the actor already has a recommendation for this
    subject, the existing row is returned unchanged. Components are
    seeded from the JVRE snapshot on first creation.
    """

    subject_user_id: uuid.UUID


class PayRecommendationComponentUpdateRequest(BaseModel):
    """Set one pay component on a recommendation.

    The override metadata block (``reason_code`` / ``role_criticality``
    / ``promotion_consideration``) is upserted on the
    ``pay_recommendation_overrides`` row for this (recommendation,
    actor) pair on every write — the screen's "Override Justification"
    panel may evolve as the actor edits multiple cells, and we want
    the latest values, not the first ones.

    Whether the value goes into ``mgr_rec_value`` (MoP authoring) or
    ``mom_rec_value`` (MoM reviewing) is decided by the service layer
    based on the actor's relationship to the subject.
    """

    value: Decimal | None = Field(default=None, ge=0)
    reason_code: str | None = Field(default=None, max_length=64)
    role_criticality: str | None = Field(default=None, max_length=16)
    promotion_consideration: bool | None = None


# ---------------------------------------------------------------------------
# Write requests — Phase 6 (MoM Pay Review)
# ---------------------------------------------------------------------------
class RecommendationReviseRequest(BaseModel):
    """Body for ``POST /pay-recommendations/{id}/revise``.

    The ``annotation_text`` is optional — when present it's appended to
    the recommendation's annotation feed under the caller's name (the
    "Christy's action: …" strip on the screen). The frontend usually
    pre-populates this with an auto-generated summary like "Promotion
    proposal was declined. Pay structure adjusted." but the MoM can
    edit before submitting.
    """

    annotation_text: str | None = Field(default=None, max_length=2000)


class AnnotationCreateRequest(BaseModel):
    """Body for ``POST /pay-recommendations/{id}/annotations`` — append
    a free-text note. Auto-attributed to the caller."""

    text: str = Field(..., min_length=1, max_length=2000)


# ---------------------------------------------------------------------------
# Dashboard — Team Risk Snapshot
# ---------------------------------------------------------------------------


class RiskSnapshotMember(BaseModel):
    subject_user_id: uuid.UUID
    subject_name: str
    manager_name: str
    manager_department: str | None
    market_gap: Decimal | None  # target_pay - current_pay


class RiskSnapshotPromoMember(BaseModel):
    subject_user_id: uuid.UUID
    subject_name: str
    manager_name: str
    current_level: str | None
    recommended_level: str | None


class RiskSnapshotCriticalMember(BaseModel):
    subject_user_id: uuid.UUID
    subject_name: str
    manager_name: str
    risk_callout_text: str | None


class RiskSnapshotGroup(BaseModel):
    manager_name: str
    manager_department: str | None
    members: list[RiskSnapshotMember]


class RiskSummaryItem(BaseModel):
    level: str
    count: int


class RiskSnapshotAllMember(BaseModel):
    subject_user_id: uuid.UUID
    subject_name: str
    current_level: str | None
    compa_ratio: Decimal | None
    market_position: str | None
    criticality: str | None
    job_title: str | None


class RiskSnapshotManagerGroup(BaseModel):
    manager_name: str
    manager_department: str | None
    members: list[RiskSnapshotAllMember]


class TeamRiskSnapshotResponse(BaseModel):
    """Aggregated IC-level risk data for the MoM dashboard."""

    summary: list[RiskSummaryItem]
    breakdown_below_market: int
    breakdown_promotion_eligible: int
    breakdown_critical_talent: int
    below_market_groups: list[RiskSnapshotGroup]
    promotion_eligible: list[RiskSnapshotPromoMember]
    critical_talent: list[RiskSnapshotCriticalMember]
    all_members_by_manager: list[RiskSnapshotManagerGroup]  # ADD THIS
