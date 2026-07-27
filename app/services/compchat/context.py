"""Layer 7 — semantic context builder.

Turns the typed tool results into the single minimal, named context
object the narrator receives. Two jobs:

* **Field stripping** — re-applies the RBAC allowlist, nulling any
  field not permitted *even if the tool returned it* (Guardrail: data
  minimisation, belt-and-suspenders over the tool-side filter).
* **Shaping** — emits a compact ``{employee, compensation, ...}`` dict
  plus a ``_sources`` list of ``{group, source, record_id}`` so the
  narrator can attribute and the validator can ground.

The context object is the *only* input the LLM sees — never an ORM row,
never the full employee profile.
"""

from __future__ import annotations

from pydantic import BaseModel

from .schemas import (
    AccessDecision,
    BudgetHeadroom,
    Comparison,
    Compensation,
    EmployeeContext,
    PayRecBase,
    Performance,
    PromotionHistory,
    Recommendation,
    Team,
    TeamAnalytics,
)

# Which context keys each comp/perf field maps to, for stripping.
_COMP_GATED = {
    "base_salary",
    "bonus_actual",
    "bonus_target_pct",
    "total_cash",
    "lti_value",
    "compa_ratio",
    "benchmark_p50",
}
_PERF_GATED = {"rating", "promotion_flag"}

# Recommendation field → the RBAC field that gates it. The equity fields
# ride on the ``lti_value`` permission (HRBP withholds equity); the rest
# ride on ``base_salary`` (always allowed for permitted roles).
_REC_GATE = {
    "unvested_usd": "lti_value",
    "next_vest_date": "lti_value",
    "months_to_next_vest": "lti_value",
}


def _strip(model: BaseModel, gated: set[str], allowed: frozenset[str]) -> dict:
    """Dump a model, nulling gated fields not in the allowlist."""
    data = model.model_dump()
    for fld in gated:
        if fld not in allowed and fld in data:
            data[fld] = None
    return data


def _strip_rec(model: BaseModel, allowed: frozenset[str]) -> dict:
    """Strip recommendation fields per their gating field."""
    data = model.model_dump()
    for fld, gate in _REC_GATE.items():
        if gate not in allowed and fld in data:
            data[fld] = None
    return data


def build_context(
    *,
    decision: AccessDecision,
    employee: EmployeeContext,
    compensation: Compensation | None = None,
    performance: Performance | None = None,
    recommendation: Recommendation | None = None,
    base_pay: PayRecBase | None = None,
    budget_headroom: BudgetHeadroom | None = None,
    team: Team | None = None,
    promotion: PromotionHistory | None = None,
    comparison: Comparison | None = None,
    analytics: TeamAnalytics | None = None,
) -> dict:
    """Assemble the minimal context object + source list."""
    allowed = decision.allowed_fields
    ctx: dict = {"employee": employee.model_dump(exclude_none=True)}
    sources: list[dict] = []

    if base_pay is not None:
        # Base-pay amounts ride on the base_salary permission. The labels
        # are deliberately explicit so the narrator never conflates the
        # JVRE recommendation with the manager's.
        bp = base_pay.model_dump()
        if "base_salary" not in allowed:
            for fld in ("current", "jvre_recommended", "manager_recommended", "mom_recommended"):
                bp[fld] = None
        ctx["base_pay"] = bp
        sources.append({"group": "base_pay", "source": base_pay.source, "record_id": base_pay.record_id})
    if recommendation is not None:
        ctx["recommendation"] = _strip_rec(recommendation, allowed)
        sources.append(
            {"group": "recommendation", "source": recommendation.source, "record_id": recommendation.record_id}
        )
    if budget_headroom is not None:
        # Rides on the base_salary permission, same as base_pay — it's a
        # pool-level figure, not the subject's personal comp, but it's
        # only meaningful alongside base pay so it follows the same gate.
        bh = budget_headroom.model_dump()
        if "base_salary" not in allowed:
            for fld in ("allocated_amount", "total_recommended", "remaining_headroom"):
                bh[fld] = None
        ctx["budget_headroom"] = bh
        sources.append(
            {"group": "budget_headroom", "source": budget_headroom.source, "record_id": budget_headroom.record_id}
        )
    if compensation is not None:
        ctx["compensation"] = _strip(compensation, _COMP_GATED, allowed)
        sources.append(
            {"group": "compensation", "source": compensation.source, "record_id": compensation.record_id}
        )
    if performance is not None:
        ctx["performance"] = _strip(performance, _PERF_GATED, allowed)
        sources.append(
            {"group": "performance", "source": performance.source, "record_id": performance.record_id}
        )
    if team is not None:
        ctx["team"] = team.model_dump()
        sources.append({"group": "team", "source": team.source, "record_id": team.record_id})
    if promotion is not None:
        ctx["promotion"] = promotion.model_dump()
        sources.append({"group": "promotion", "source": promotion.source, "record_id": promotion.record_id})
    if comparison is not None:
        comp = comparison.model_dump()
        comp["employee_a"] = _strip(comparison.employee_a, _COMP_GATED, allowed)
        comp["employee_b"] = _strip(comparison.employee_b, _COMP_GATED, allowed)
        ctx["comparison"] = comp
        sources.append(
            {"group": "comparison", "source": comparison.employee_a.source, "record_id": comparison.employee_a.record_id}
        )
    if analytics is not None:
        ctx["analytics"] = analytics.model_dump()
        sources.append({"group": "analytics", "source": analytics.source, "record_id": analytics.record_id})

    ctx["_sources"] = sources
    if decision.reason:
        ctx["_access_note"] = decision.reason
    return ctx
