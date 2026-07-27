"""Unit tests for the CompChat pipeline's deterministic layers.

These cover the parts that must hold *without* an LLM or a database:
the structural tool-selection guarantee, the rules-first classifier,
field-level RBAC, context stripping, and numeric grounding. The
DB-backed tool queries and the SLM paths are exercised separately
(integration) and are intentionally out of scope here.
"""

from __future__ import annotations

import uuid

import pytest
from app.core.authorization import RoleProfile
from app.core.roles import RoleCode
from app.dependencies.tenant_dependency import TenantContext
from app.services.compchat import context as context_builder
from app.services.compchat import intent, rbac
from app.services.compchat.schemas import (
    AccessState,
    Compensation,
    EmployeeContext,
    IntentType,
    PayRecBase,
    Recommendation,
)
from app.services.compchat.tools import INTENT_TOOLS
from app.services.compchat.validator import grounded_numbers, validate_numbers


# ---------------------------------------------------------------------------
# Layer 5 — structural tool-selection guarantee
# ---------------------------------------------------------------------------
def test_team_query_cannot_reach_compensation_tool():
    """The headline guardrail: a TEAM_QUERY's permitted set never
    contains the compensation tool, regardless of question wording."""
    assert "get_compensation" not in INTENT_TOOLS[IntentType.TEAM_QUERY]
    assert "get_compensation" not in INTENT_TOOLS[IntentType.PROMOTION_QUERY]
    assert "get_compensation" in INTENT_TOOLS[IntentType.COMPENSATION_QUERY]


def test_every_real_intent_has_a_tool_set():
    for it in IntentType:
        assert it in INTENT_TOOLS
    assert INTENT_TOOLS[IntentType.UNKNOWN] == ()


# ---------------------------------------------------------------------------
# Layer 4 — rules-first classifier
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What is her base salary this year?", IntentType.COMPENSATION_QUERY),
        ("Show me the bonus breakdown", IntentType.COMPENSATION_QUERY),
        ("What was his performance rating?", IntentType.PERFORMANCE_QUERY),
        ("When was she last promoted?", IntentType.PROMOTION_QUERY),
        ("Who are my direct reports?", IntentType.TEAM_QUERY),
        ("Compare her pay versus Rahul", IntentType.COMPARISON_QUERY),
        ("What's the median salary across the team?", IntentType.ANALYTICS_QUERY),
        ("What's the weather today?", IntentType.UNKNOWN),
    ],
)
def test_rules_classify(question, expected):
    assert intent._rules_classify(question) is expected


def test_fallback_from_history_inherits_prior_intent():
    """A correction like 'that is the manager rec, not the jvre' carries
    no intent itself; it must inherit the conversation's topic."""
    prior = ["what is base pay", "what is jvre rec base pay"]
    assert intent.fallback_from_history(prior) is IntentType.COMPENSATION_QUERY
    assert intent.fallback_from_history(["hello there"]) is IntentType.UNKNOWN


@pytest.mark.asyncio
async def test_classify_fast_path_skips_slm(monkeypatch):
    """A confident single-subject keyword hit must not call the SLM."""

    async def _boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("SLM should not be called on the fast path")

    monkeypatch.setattr(intent.slm, "complete_json", _boom)
    result = await intent.classify("http://x", "model", "what is her salary?")
    assert result.intent is IntentType.COMPENSATION_QUERY


# ---------------------------------------------------------------------------
# Layer 2 — field-level 3-state RBAC
# ---------------------------------------------------------------------------
def _ctx(roles: set[str], user_id: uuid.UUID) -> TenantContext:
    tenant_id = uuid.uuid4()

    class _U:
        id = user_id

    return TenantContext(
        user=_U(),
        active_tenant_id=tenant_id,
        role_profile=RoleProfile(tenant_id=tenant_id, tenant_roles=frozenset(roles)),
    )


@pytest.mark.asyncio
async def test_rbac_self_sees_all_fields():
    uid = uuid.uuid4()
    decision = await rbac.can_access(None, _ctx({RoleCode.MANAGER}, uid), uid)
    assert decision.state is AccessState.ALLOW
    assert "lti_value" in decision.allowed_fields


@pytest.mark.asyncio
async def test_rbac_hr_is_tenant_wide_allow():
    decision = await rbac.can_access(None, _ctx({RoleCode.HR}, uuid.uuid4()), uuid.uuid4())
    assert decision.state is AccessState.ALLOW
    assert decision.allowed_fields == rbac.ALL_FIELDS


@pytest.mark.asyncio
async def test_rbac_hrbp_partial_withholds_lti(monkeypatch):
    monkeypatch.setattr(rbac, "_subject_in_chain", _async_true)
    decision = await rbac.can_access(None, _ctx({RoleCode.HRBP}, uuid.uuid4()), uuid.uuid4())
    assert decision.state is AccessState.PARTIAL_ACCESS
    assert "lti_value" not in decision.allowed_fields
    assert "lti_value" in decision.denied_fields


@pytest.mark.asyncio
async def test_rbac_unprivileged_role_denied():
    # A tenant role outside every comp-granting set is denied before any
    # DB row check (so passing db=None is safe).
    decision = await rbac.can_access(None, _ctx(set(), uuid.uuid4()), uuid.uuid4())
    assert decision.state is AccessState.DENY
    assert decision.allowed_fields == frozenset()


async def _async_true(*a, **k):
    return True


# ---------------------------------------------------------------------------
# Layer 7 — context stripping
# ---------------------------------------------------------------------------
def test_context_builder_strips_denied_fields():
    decision = rbac.AccessDecision(
        state=AccessState.PARTIAL_ACCESS,
        allowed_fields=rbac.ALL_FIELDS - {"lti_value"},
        denied_fields={"lti_value"} | rbac.HARD_BLOCKED,
        reason="Equity (LTI) withheld for this role.",
    )
    emp = EmployeeContext(name="A", role="Eng", level="L4", department="R&D", job_family="SW")
    comp = Compensation(
        base_salary=1_800_000, lti_value=500_000, source="tessot_base_data", record_id="COMP_X_2026"
    )
    ctx = context_builder.build_context(decision=decision, employee=emp, compensation=comp)
    assert ctx["compensation"]["base_salary"] == 1_800_000
    assert ctx["compensation"]["lti_value"] is None  # stripped
    assert ctx["_sources"][0]["record_id"] == "COMP_X_2026"
    assert ctx["_access_note"]


# ---------------------------------------------------------------------------
# Layer 8 — numeric grounding
# ---------------------------------------------------------------------------
def test_grounded_numbers_walks_nested_context():
    ctx = {"compensation": {"base_salary": 1_800_000, "bonus_actual": 220_000}}
    nums = grounded_numbers(ctx)
    assert 1_800_000 in nums
    assert 220_000 in nums


def test_validate_numbers_passes_grounded_answer():
    ctx = {"compensation": {"base_salary": 1_800_000}}
    ok, ungrounded = validate_numbers("Her base salary is ₹18,00,000.", ctx)
    assert ok
    assert ungrounded == []


def test_validate_numbers_blocks_fabricated_figure():
    ctx = {"compensation": {"base_salary": 1_800_000}}
    ok, ungrounded = validate_numbers("Her base salary is ₹25,00,000.", ctx)
    assert not ok
    assert "25,00,000" in ungrounded


def test_validate_numbers_ignores_small_numbers():
    """Counts / single-digit values below the magnitude floor are not
    blocked (they are structural, not fabricated money)."""
    ctx = {"team": {"span_direct": 5}}
    ok, _ = validate_numbers("You have 5 direct reports and 2 open roles.", ctx)
    assert ok


def test_rationale_numbers_are_grounded():
    """A figure stated in the displayed rationale is a valid grounding
    source for a follow-up answer (the JVRE-rec regression)."""
    grounding = {"_rationale": "...bringing his base to ₹185,300, a 28.32% increase..."}
    ok, ungrounded = validate_numbers("The recommended new base is ₹1,85,300.", grounding)
    assert ok
    assert ungrounded == []


# ---------------------------------------------------------------------------
# Recommendation context (JVRE engine output anchor)
# ---------------------------------------------------------------------------
def _partial_decision() -> rbac.AccessDecision:
    return rbac.AccessDecision(
        state=AccessState.PARTIAL_ACCESS,
        allowed_fields=rbac.ALL_FIELDS - {"lti_value"},
        denied_fields={"lti_value"} | rbac.HARD_BLOCKED,
        reason="Equity (LTI) withheld for this role.",
    )


_EMP = EmployeeContext(name="Eddie", role="Eng", level="L5", department="R&D", job_family="SW")


def test_recommendation_context_strips_equity_on_partial():
    rec = Recommendation(
        compa_ratio=1.05,
        unvested_usd=50_000,
        next_vest_date="2027-06-01",
        source="iquest_engine_output",
        record_id="JVRE_EMP1_C1",
    )
    ctx = context_builder.build_context(decision=_partial_decision(), employee=_EMP, recommendation=rec)
    assert ctx["recommendation"]["compa_ratio"] is not None  # visible
    assert ctx["recommendation"]["unvested_usd"] is None  # equity withheld
    assert ctx["recommendation"]["next_vest_date"] is None


def test_base_pay_keeps_jvre_and_manager_distinct():
    """The bug from the transcript: JVRE rec vs manager rec must be
    separately labeled, not collapsed to one number."""
    decision = rbac.AccessDecision(state=AccessState.ALLOW, allowed_fields=rbac.ALL_FIELDS)
    base = PayRecBase(
        current=142_600,
        jvre_recommended=151_900,
        manager_recommended=148_000,
        mom_recommended=151_900,
        source="pay_recommendation_components",
        record_id="PAYREC_x_BASE_PAY",
    )
    ctx = context_builder.build_context(decision=decision, employee=_EMP, base_pay=base)
    assert ctx["base_pay"]["jvre_recommended"] == 151_900
    assert ctx["base_pay"]["manager_recommended"] == 148_000  # distinct from JVRE
    assert ctx["_sources"][0]["source"] == "pay_recommendation_components"


def test_base_pay_withheld_without_base_salary_permission():
    decision = rbac.AccessDecision(state=AccessState.PARTIAL_ACCESS, allowed_fields=frozenset({"rating"}))
    base = PayRecBase(current=142_600, jvre_recommended=151_900, source="pay_recommendation_components", record_id="r")
    ctx = context_builder.build_context(decision=decision, employee=_EMP, base_pay=base)
    assert ctx["base_pay"]["current"] is None
    assert ctx["base_pay"]["jvre_recommended"] is None
