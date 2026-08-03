"""The CompChat pipeline orchestrator (ties Layers 1-11 together).

Two public coroutines:

* :func:`prepare` — runs Layers 2-7 (RBAC, resolver, intent, tools,
  context) and returns a :class:`PipelineResult` carrying either a
  narration prompt (happy path) or a ``terminal_message`` (hard stop).
* :func:`narrate` — Layers 8-11: buffers the LLM narration, runs the
  numeric grounding check, then yields SSE token strings (the verified
  answer or a structured block message), and writes the audit trace.

Layer 1 (auth) and the tenant/RLS scoping happen in the router via the
existing dependencies, before either coroutine is called.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts import strip_react_scaffold
from app.core.config import Settings
from app.dependencies.tenant_dependency import TenantContext
from app.services import audit_log_service

from . import context as context_builder
from . import intent as intent_classifier
from . import prompts, rbac, resolver, slm, tools
from .schemas import (
    AnalyticsScope,
    Classification,
    IntentType,
    PipelineResult,
    ResolvedSubject,
)
from .validator import grounding_index, validation_report

logger = logging.getLogger(__name__)

# Chunk size when re-emitting a buffered, validated answer over SSE.
_EMIT_CHUNK = 24


def _terminal(trace_id: str, intent: IntentType, message: str, **kw) -> PipelineResult:
    return PipelineResult(trace_id=trace_id, intent=intent, terminal_message=message, **kw)


async def prepare(
    db: AsyncSession,
    ctx: TenantContext,
    settings: Settings,
    *,
    question: str,
    subject_user_id: uuid.UUID | None,
    cycle_id: uuid.UUID | None,
    fiscal_year_hint: int | None,
    rationale_text: str | None,
    allow_entity_switch: bool = True,
    history: list[tuple[str, str]] | None = None,
) -> PipelineResult:
    """Layers 2-7. Returns a result ready to narrate, or a hard stop.

    ``allow_entity_switch`` controls whether a name in the question may
    redirect the answer to a *different* employee. The recommendation
    panel sets this False so the chat stays anchored to the open subject;
    a "compare to others at this level" question is then served as a
    peer-aggregate ANALYTICS_QUERY rather than by naming an individual.
    """
    trace_id = uuid.uuid4().hex

    # --- Layer 4: intent ---
    cls: Classification = await intent_classifier.classify(settings, question)
    # A follow-up with no intent of its own (a correction, "why?") inherits
    # the topic of the conversation rather than dropping to out-of-scope.
    if cls.intent is IntentType.UNKNOWN and history:
        prior = [c for r, c in history if r == "user"]
        carried = intent_classifier.fallback_from_history(prior)
        if carried is not IntentType.UNKNOWN:
            cls = cls.model_copy(update={"intent": carried})
    if cls.intent is IntentType.UNKNOWN:
        return _terminal(trace_id, cls.intent, prompts.out_of_scope())

    # --- Population-level batch report: hand back a download link. The
    # PDF is computed lazily at the endpoint, RBAC-scoped to the caller. ---
    if cls.intent is IntentType.REPORT_REQUEST:
        cycle_q = f"?cycle_id={cycle_id}" if cycle_id is not None else ""
        url = f"/ai/reports/compensation.pdf{cycle_q}"
        result = _terminal(trace_id, cls.intent, prompts.report_ready(url))
        result.tools_called = ["build_report"]
        return result

    # When the panel is locked to its subject, a named comparison becomes
    # a peer-aggregate query and stray names are ignored (no cross-employee
    # disclosure).
    if not allow_entity_switch:
        if cls.intent is IntentType.COMPARISON_QUERY:
            cls = cls.model_copy(
                update={"intent": IntentType.ANALYTICS_QUERY, "analytics_scope": AnalyticsScope.JOB_FAMILY}
            )
        cls = cls.model_copy(update={"secondary_name": None})

    # --- Layer 3: resolve subject(s) ---
    # An in-conversation entity switch (a name without a comparison)
    # re-resolves the primary subject; a comparison keeps the injected
    # subject as A and resolves the named party as B.
    subject_b: ResolvedSubject | None = None
    if cls.secondary_name and cls.intent is not IntentType.COMPARISON_QUERY:
        outcome = await resolver.resolve_by_name(db, ctx.active_tenant_id, cls.secondary_name)
    elif subject_user_id is not None:
        outcome = await resolver.resolve_injected(db, ctx.active_tenant_id, subject_user_id)
    elif cls.secondary_name:
        outcome = await resolver.resolve_by_name(db, ctx.active_tenant_id, cls.secondary_name)
    else:
        return _terminal(trace_id, cls.intent, prompts.not_found())

    if outcome.status == "AMBIGUOUS":
        return _terminal(trace_id, cls.intent, prompts.ambiguous(outcome.candidates))
    if outcome.status == "NOT_FOUND" or outcome.subject is None:
        return _terminal(trace_id, cls.intent, prompts.not_found())
    subject = outcome.subject

    if cls.intent is IntentType.COMPARISON_QUERY:
        if not cls.secondary_name:
            return _terminal(trace_id, cls.intent, prompts.out_of_scope())
        b_outcome = await resolver.resolve_by_name(db, ctx.active_tenant_id, cls.secondary_name)
        if b_outcome.status == "AMBIGUOUS":
            return _terminal(trace_id, cls.intent, prompts.ambiguous(b_outcome.candidates))
        if b_outcome.status != "RESOLVED" or b_outcome.subject is None:
            return _terminal(trace_id, cls.intent, prompts.not_found(cls.secondary_name))
        subject_b = b_outcome.subject

    # --- Layer 2: RBAC (before any retrieval) ---
    decision = await rbac.can_access(db, ctx, subject.user_id)
    if decision.is_denied:
        return _terminal(
            trace_id, cls.intent, prompts.denied(decision.reason),
            rbac_state=decision.state, resolved_user_id=subject.user_id,
        )
    if subject_b is not None:
        decision_b = await rbac.can_access(db, ctx, subject_b.user_id)
        if decision_b.is_denied:
            return _terminal(
                trace_id, cls.intent, prompts.denied(decision_b.reason),
                rbac_state=decision_b.state, resolved_user_id=subject.user_id,
            )

    # --- Layer 6: data retrieval (deterministic tools) ---
    fiscal_year = (
        cls.fiscal_year
        or fiscal_year_hint
        or await tools.default_fiscal_year(db, ctx.active_tenant_id)
        or settings.compchat_default_fiscal_year
    )
    tenant_id = ctx.active_tenant_id
    eid = subject.employee_id

    employee = await tools.get_employee_context(db, tenant_id, eid, fiscal_year)
    if employee is None:
        return _terminal(
            trace_id, cls.intent, prompts.data_unavailable(),
            rbac_state=decision.state, resolved_user_id=subject.user_id,
        )

    tools_called = ["get_employee_context"]
    build_kwargs: dict = {"decision": decision, "employee": employee}

    # The recommendation (JVRE engine output) is the panel's anchor: it
    # carries current + recommended pay, benchmarks, vesting and scores —
    # the facts the rationale discusses. Always include it so those
    # questions are answerable and grounded. COMPENSATION_QUERY relies on
    # it entirely (no separate Tessot current-comp call, which would
    # introduce a second, slightly different "current base").
    recommendation = await tools.get_recommendation(db, tenant_id, subject.user_id)
    if recommendation is not None:
        build_kwargs["recommendation"] = recommendation
        tools_called.append("get_recommendation")

    # Authoritative base-pay split (current / JVRE / manager / MoM) from
    # the MoP-owned recommendation; engine number as fallback.
    base_pay = None
    if cycle_id is not None:
        base_pay = await tools.get_pay_recommendation_base(db, tenant_id, cycle_id, subject.user_id)
    if base_pay is None:
        base_pay = await tools.get_engine_base(db, tenant_id, subject.user_id)
    if base_pay is not None:
        build_kwargs["base_pay"] = base_pay
        tools_called.append("get_pay_recommendation_base")

    # Manager's remaining, uncommitted budget pool this cycle — answers
    # "is there room in the budget" as a distinct, grounded fact from the
    # market-benchmark headroom already in `recommendation`.
    if cycle_id is not None:
        budget_headroom = await tools.get_budget_headroom(db, tenant_id, cycle_id, subject.user_id)
        if budget_headroom is not None:
            build_kwargs["budget_headroom"] = budget_headroom
            tools_called.append("get_budget_headroom")

    if cls.intent is IntentType.PERFORMANCE_QUERY:
        build_kwargs["performance"] = await tools.get_performance(db, tenant_id, eid, fiscal_year)
        tools_called.append("get_performance")
    elif cls.intent is IntentType.PROMOTION_QUERY:
        build_kwargs["promotion"] = await tools.get_promotion_history(db, tenant_id, eid, fiscal_year)
        tools_called.append("get_promotion_history")
    elif cls.intent is IntentType.TEAM_QUERY:
        build_kwargs["team"] = await tools.get_team(db, tenant_id, eid, fiscal_year)
        tools_called.append("get_team")
    elif cls.intent is IntentType.COMPARISON_QUERY and subject_b is not None:
        build_kwargs["comparison"] = await tools.compare_compensation(
            db, tenant_id, eid, subject_b.employee_id, fiscal_year,
            name_a=subject.name, name_b=subject_b.name,
        )
        tools_called.append("compare_compensation")
    elif cls.intent is IntentType.ANALYTICS_QUERY:
        scope = cls.analytics_scope or AnalyticsScope.TEAM
        build_kwargs["analytics"] = await tools.get_analytics(
            db, tenant_id, eid, fiscal_year, scope=scope
        )
        tools_called.append("get_analytics")

    # Layer 6 early return. For an intent with a Tessot primary tool,
    # unavailable if that tool returned nothing. COMPENSATION_QUERY has no
    # primary — it rides the recommendation/base-pay anchor, so it's
    # unavailable only when neither anchor produced data.
    primary = {
        IntentType.PERFORMANCE_QUERY: "performance",
        IntentType.PROMOTION_QUERY: "promotion",
        IntentType.TEAM_QUERY: "team",
        IntentType.COMPARISON_QUERY: "comparison",
        IntentType.ANALYTICS_QUERY: "analytics",
    }.get(cls.intent)
    anchor_missing = recommendation is None and base_pay is None
    unavailable = (
        build_kwargs.get(primary) is None if primary is not None else anchor_missing
    )
    if unavailable:
        return _terminal(
            trace_id, cls.intent, prompts.data_unavailable(),
            rbac_state=decision.state, resolved_user_id=subject.user_id, tools_called=tools_called,
        )

    # --- Layer 7: minimal context ---
    ctx_obj = context_builder.build_context(**build_kwargs)
    narration_prompt = prompts.build_narration_prompt(question, ctx_obj, rationale_text, history)

    # --- Agent trace, steps 1-2 (DB fetch + context/token assembly). The
    # narration + validation steps are appended in narrate(). Each step
    # carries a plain-English ``summary`` so the trace reads as a BTS
    # narrative of the workflow when scanned top to bottom. ---
    context_groups = [k for k in ctx_obj if not k.startswith("_")]
    sources = ctx_obj.get("_sources", [])
    index = grounding_index(ctx_obj)
    # The "token": every DB value tagged with the record it came from. This
    # is the ground truth the LLM's output is later checked against.
    grounding_token = [
        {"value": val, "field": srcs[0].get("field"), "record_id": srcs[0].get("record_id")}
        for val, srcs in sorted(index.items())
        if srcs
    ]
    agent_trace = [
        {
            "step": 1,
            "name": "db_fetch",
            "layer": "6 — deterministic tools (no LLM)",
            "summary": (
                f"Ran {len(tools_called)} deterministic tool(s) "
                f"({', '.join(tools_called)}) against the tenant-scoped DB and got "
                f"{len(sources)} source record(s). The LLM never touches the DB — "
                f"only these tools do."
            ),
            "tools_called": tools_called,
            "records": sources,
        },
        {
            "step": 2,
            "name": "build_grounding_token",
            "layer": "7 — minimal context + narration prompt",
            "summary": (
                f"Assembled the grounding token from the DB results: {len(context_groups)} "
                f"group(s) ({', '.join(context_groups)}) carrying {len(grounding_token)} "
                f"verifiable value(s), each tagged to its source record. Built the SLM "
                f"prompt (~{len(narration_prompt) // 4} tokens). This token is the ONLY "
                f"data the LLM may use."
            ),
            "context_groups": context_groups,
            "grounding_token": grounding_token,
            "verifiable_value_count": len(grounding_token),
            "prompt_chars": len(narration_prompt),
            "approx_prompt_tokens": len(narration_prompt) // 4,
        },
    ]

    return PipelineResult(
        trace_id=trace_id,
        intent=cls.intent,
        tools_called=tools_called,
        rationale_text=rationale_text,
        rbac_state=decision.state,
        resolved_user_id=subject.user_id,
        narration_prompt=narration_prompt,
        context_obj=ctx_obj,
        agent_trace=agent_trace,
    )


async def _audit(
    db: AsyncSession, ctx: TenantContext, result: PipelineResult
) -> None:
    await audit_log_service.log_action(
        db,
        actor_user_id=ctx.user.id,
        action="COMPCHAT_QUERY",
        tenant_id=ctx.active_tenant_id,
        resource_type="employee",
        resource_id=str(result.resolved_user_id) if result.resolved_user_id else None,
        metadata={
            "trace_id": result.trace_id,
            "intent": result.intent.value,
            "tools_called": result.tools_called,
            "rbac_state": result.rbac_state.value if result.rbac_state else None,
            "response_generated": result.response_generated,
            "agent_trace": result.agent_trace,
        },
    )


async def narrate(
    db: AsyncSession,
    ctx: TenantContext,
    settings: Settings,
    result: PipelineResult,
) -> AsyncIterator[str]:
    """Layers 8-11: narrate (buffered) → validate → emit SSE → audit.

    Yields raw text chunks; the router wraps them as SSE ``data:`` events.
    """
    # Hard stop: stream the structured terminal message verbatim. No DB
    # fetch / narration happened (the stop fired earlier in prepare), so
    # the trace records only the halt.
    if result.terminal_message is not None:
        result.response_generated = False
        result.agent_trace.append(
            {
                "step": 3,
                "name": "halt",
                "layer": "2-7 — stopped before narration",
                "summary": (
                    "Workflow halted before the LLM ran: a gate (RBAC denial, "
                    "ambiguous subject, out-of-scope, or missing data) returned a "
                    "structured message. No LLM output, nothing to verify."
                ),
                "reason": "terminal_message (denied / ambiguous / out-of-scope / data unavailable)",
                "narrated": False,
            }
        )
        yield result.terminal_message
        await _audit(db, ctx, result)
        return

    # Buffer the full narration so the numeric check can block it before
    # any token reaches the user (SSE cannot retract).
    buffer_parts: list[str] = []
    try:
        async for token in slm.stream_generate(
            settings,
            result.narration_prompt or "",
            temperature=settings.bedrock_temperature,
        ):
            buffer_parts.append(token)
    except Exception:
        logger.exception("compchat narration failed (trace=%s)", result.trace_id)
        result.response_generated = False
        result.agent_trace.append(
            {
                "step": 3,
                "name": "narration",
                "layer": "8 — LLM narrate (buffered)",
                "summary": "The LLM call failed (transport/timeout); fell back to a structured 'data unavailable' message.",
                "generated": False,
                "error": "llm_narration_failed",
            }
        )
        yield prompts.data_unavailable()
        await _audit(db, ctx, result)
        return

    # The model doesn't always keep the ReAct scratchpad silent — strip any
    # leaked "## Answer / ## Reasoning" or "*Reason:* ... *Answer:*" scaffold
    # before it's validated or shown to the manager.
    answer = strip_react_scaffold("".join(buffer_parts).strip())
    # Ground against the context AND the rationale: the rationale is shown
    # verbatim to the manager, so the numbers it states are legitimate
    # grounding sources for follow-up questions about the recommendation.
    grounding = dict(result.context_obj or {})
    if result.rationale_text:
        grounding["_rationale"] = result.rationale_text

    # --- Trace step 3: the LLM narration produced from the grounding token ---
    result.agent_trace.append(
        {
            "step": 3,
            "name": "narration",
            "layer": "8 — LLM narrate (buffered)",
            "summary": (
                f"The LLM ({settings.bedrock_model_id}) rephrased the grounding token into a "
                f"{len(answer)}-char answer. It only narrates — it cannot fetch or compute; "
                f"its numbers are claims to be verified next, not trusted facts."
            ),
            "model": settings.bedrock_model_id,
            "generated": bool(answer),
            "answer_chars": len(answer),
            "answer_preview": answer[:500],
        }
    )

    # --- Trace step 4: the verification harness. Each high-magnitude number
    # the SLM emitted is traced back to the DB record that backs it and
    # tagged VERIFIED or UNGROUNDED; any UNGROUNDED figure blocks the whole
    # answer. ``comparisons`` is the value-by-value evidence. ---
    vreport = validation_report(answer, grounding)
    result.agent_trace.append(
        {
            "step": 4,
            "name": "verification_harness",
            "layer": "9 — verify SLM numbers against DB tokens",
            "grounding_includes_rationale": bool(result.rationale_text),
            **vreport,
        }
    )
    ok, ungrounded = vreport["ok"], vreport["ungrounded"]
    if not ok or not answer:
        logger.warning(
            "compchat numeric grounding blocked answer (trace=%s, ungrounded=%s)",
            result.trace_id, ungrounded,
        )
        result.response_generated = False
        yield prompts.blocked_validation()
        await _audit(db, ctx, result)
        return

    result.response_generated = True
    for i in range(0, len(answer), _EMIT_CHUNK):
        yield answer[i : i + _EMIT_CHUNK]
    await _audit(db, ctx, result)
