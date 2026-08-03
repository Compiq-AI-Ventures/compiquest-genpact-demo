"""Typed objects for the CompChat pipeline.

Three families live here:

* **Enums** — :class:`IntentType`, :class:`AccessState`.
* **Control objects** — what flows *between* layers: the LLM's
  :class:`Classification`, the RBAC :class:`AccessDecision`, the
  resolver's :class:`ResolverOutcome`, and the final
  :class:`PipelineResult`.
* **Tool result objects** — the typed return of each of the six tools
  (Layer 5). These are the *only* shapes a tool may return — never a
  raw ORM row or a JSON dump of the full record (Guardrail: data
  minimisation). Every fact-bearing tool result carries ``source`` and
  ``record_id`` so the narrator can attribute (Guardrail 3) and the
  validator can ground (Guardrail 4).
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class IntentType(enum.StrEnum):
    """The six intent types from the framework, plus an out-of-scope
    sentinel. The classifier is grammar-constrained to these values."""

    COMPENSATION_QUERY = "COMPENSATION_QUERY"
    PERFORMANCE_QUERY = "PERFORMANCE_QUERY"
    PROMOTION_QUERY = "PROMOTION_QUERY"
    TEAM_QUERY = "TEAM_QUERY"
    COMPARISON_QUERY = "COMPARISON_QUERY"
    ANALYTICS_QUERY = "ANALYTICS_QUERY"
    REPORT_REQUEST = "REPORT_REQUEST"
    UNKNOWN = "UNKNOWN"


class AnalyticsScope(enum.StrEnum):
    """Grouping axis for an ANALYTICS_QUERY."""

    TEAM = "TEAM"
    JOB_FAMILY = "JOB_FAMILY"


class AccessState(enum.StrEnum):
    """Three-state RBAC result (framework Layer 2)."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    PARTIAL_ACCESS = "PARTIAL_ACCESS"


# ---------------------------------------------------------------------------
# Layer 4 output — the SLM's only structured decision
# ---------------------------------------------------------------------------
class Classification(BaseModel):
    """Classifier output (Layer 4).

    This is the exact JSON shape the model is prompted to emit (see
    ``intent._CLASSIFY_SCHEMA`` / ``slm.complete_json``). It carries the
    intent plus the only free-text slots the model must read: a
    comparison/secondary target name and the analytics grouping.
    Everything else (subject id, fiscal year) the pipeline supplies
    deterministically.
    """

    intent: IntentType = Field(description="One of the six intent types, or UNKNOWN.")
    secondary_name: str | None = Field(
        default=None,
        description="A second employee named in the question (the comparison "
        "target, or an in-conversation entity switch). Null if none.",
    )
    fiscal_year: int | None = Field(
        default=None, description="Explicit fiscal year if the question names one; else null."
    )
    analytics_scope: AnalyticsScope | None = Field(
        default=None, description="For ANALYTICS_QUERY only: TEAM or JOB_FAMILY."
    )


# ---------------------------------------------------------------------------
# Layer 2 output
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AccessDecision:
    """Result of ``rbac.can_access`` for one (requester, subject) pair.

    ``allowed_fields`` is the field allowlist the tools must filter to.
    For ALLOW it's the full permitted set for the role; for
    PARTIAL_ACCESS it's a strict subset; for DENY it's empty.
    """

    state: AccessState
    allowed_fields: frozenset[str] = field(default_factory=frozenset)
    denied_fields: frozenset[str] = field(default_factory=frozenset)
    reason: str = ""

    @property
    def is_denied(self) -> bool:
        return self.state is AccessState.DENY


# ---------------------------------------------------------------------------
# Layer 3 output
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ResolvedSubject:
    """A fully resolved employee. After resolution only ``user_id``
    flows through RBAC; ``employee_id`` is the Tessot string key the
    tools query by; ``name`` is carried for narration only."""

    user_id: uuid.UUID
    employee_id: str
    name: str


@dataclass(frozen=True)
class ResolverOutcome:
    """Resolver result. Exactly one of the three states is meaningful:

    * ``RESOLVED`` — ``subject`` set, proceed.
    * ``AMBIGUOUS`` — ``candidates`` set, STOP and ask for clarification.
    * ``NOT_FOUND`` — no match; STOP with a not-found message.
    """

    status: str  # "RESOLVED" | "AMBIGUOUS" | "NOT_FOUND"
    subject: ResolvedSubject | None = None
    candidates: tuple[ResolvedSubject, ...] = ()


# ---------------------------------------------------------------------------
# Layer 5/6 — typed tool results
# ---------------------------------------------------------------------------
class EmployeeContext(BaseModel):
    """``get_employee_context`` — base context injected for every intent."""

    name: str
    role: str  # designation
    level: str
    manager: str | None = None
    department: str
    job_family: str
    hire_date: str | None = None


class Compensation(BaseModel):
    """``get_compensation`` — fields gated by the RBAC allowlist; any
    field the allowlist excludes is set to ``None`` by the context
    builder before the narrator sees it."""

    base_salary: int | None = None
    bonus_actual: int | None = None
    bonus_target_pct: float | None = None
    total_cash: int | None = None
    lti_value: int | None = None
    compa_ratio: float | None = None
    benchmark_p50: int | None = None
    currency: str = "INR"
    source: str
    record_id: str


class Performance(BaseModel):
    """``get_performance``."""

    rating: float | None = None
    promotion_flag: bool | None = None
    source: str
    record_id: str


class PayRecBase(BaseModel):
    """``get_pay_recommendation_base`` — the four distinct base-pay
    figures the Pay Recommendation panel tracks for one subject, read
    from the MoP-owned ``PayRecommendationComponent`` (BASE_PAY). Keeping
    them separate lets the chat answer "JVRE rec base" vs "manager rec
    base" precisely instead of conflating them. ``source`` is
    ``pay_recommendation_components`` for the authoritative split, or
    ``iquest_engine_output`` when only the engine number is available."""

    current: int | None = None
    jvre_recommended: int | None = None
    manager_recommended: int | None = None  # MoP
    mom_recommended: int | None = None
    currency: str = "INR"
    source: str
    record_id: str


class Recommendation(BaseModel):
    """``get_recommendation`` — the JVRE engine output the rationale is
    about (benchmarks, compa-ratio, equity/vesting, JVRE scores). The
    base-pay amounts live in :class:`PayRecBase` instead, so the two are
    never conflated. Equity fields are gated by the RBAC allowlist
    (``lti_value``)."""

    compa_ratio: float | None = None
    new_compa_ratio_after_rec: float | None = None
    target_bonus_pct: float | None = None
    total_cash: int | None = None
    rec_total_cash: int | None = None
    benchmark_p25: int | None = None
    benchmark_p50: int | None = None
    benchmark_p75: int | None = None
    months_since_last_increase: int | None = None
    unvested_usd: int | None = None
    next_vest_date: str | None = None
    months_to_next_vest: float | None = None
    jvre_score: float | None = None
    jvre_tier: str | None = None
    rating_band: str | None = None
    promotion_flag: bool | None = None
    currency: str = "INR"
    source: str
    record_id: str


class BudgetHeadroom(BaseModel):
    """``get_budget_headroom`` — the subject's manager's remaining,
    unspent budget pool for this cycle: how much of the pool the
    manager was allocated is still uncommitted after summing every
    direct report's manager-recommended base pay. Answers "is there
    room in the budget", not "is this competitive vs market" (that's
    :class:`Recommendation`'s benchmark fields) — the two are
    deliberately separate facts so the narrator never conflates them.
    """

    allocated_amount: int | None = None
    total_recommended: int | None = None
    remaining_headroom: int | None = None
    currency: str = "INR"
    source: str
    record_id: str


class TeamMember(BaseModel):
    employee_id: str
    name: str
    level: str


class Team(BaseModel):
    """``get_team``."""

    direct_reports: list[TeamMember]
    span_direct: int
    span_indirect: int
    source: str
    record_id: str


class PromotionEvent(BaseModel):
    fiscal_year: int
    from_level: str
    to_level: str
    date: str | None = None


class PromotionHistory(BaseModel):
    """``get_promotion_history``."""

    promotions: list[PromotionEvent]
    months_since_last: int | None = None
    source: str
    record_id: str


class Comparison(BaseModel):
    """``compare_compensation`` — two employees side by side."""

    employee_a: Compensation
    employee_b: Compensation
    name_a: str
    name_b: str
    delta_salary: int | None = None
    delta_bonus: int | None = None


class TeamAnalytics(BaseModel):
    """``get_analytics`` — aggregate over a team or job family. No
    individual rows leave the tool; only aggregates."""

    scope: AnalyticsScope
    group_label: str
    headcount: int
    avg_base_salary: int | None = None
    median_base_salary: int | None = None
    avg_compa_ratio: float | None = None
    source: str
    record_id: str


# ---------------------------------------------------------------------------
# Pipeline result
# ---------------------------------------------------------------------------
@dataclass
class PipelineResult:
    """Everything the router needs after the pipeline runs.

    For a happy path, ``system_prompt`` + ``context_json`` feed the
    streaming narrator and ``answer`` is empty until streamed. For a
    hard stop (ambiguity, denial, out-of-scope, validation failure),
    ``terminal_message`` carries the structured non-LLM response and the
    router streams that verbatim — never an LLM-generated value.
    """

    trace_id: str
    intent: IntentType
    tools_called: list[str] = field(default_factory=list)
    rbac_state: AccessState | None = None
    resolved_user_id: uuid.UUID | None = None
    # Narration inputs (set only when we will call the SLM):
    narration_prompt: str | None = None
    context_obj: dict | None = None
    # Rationale shown to the manager; its numbers are also grounded.
    rationale_text: str | None = None
    # Hard-stop short circuit (set instead of narration inputs):
    terminal_message: str | None = None
    # Filled by the validator after streaming completes:
    response_generated: bool = False
    # Step-by-step agent execution trace (db fetch -> context/token ->
    # narration -> numeric validation). Assembled across prepare()/narrate()
    # and written into the COMPCHAT_QUERY audit row's metadata.
    agent_trace: list[dict] = field(default_factory=list)
