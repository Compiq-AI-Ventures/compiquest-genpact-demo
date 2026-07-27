"""Compensation report builder — Phase 2.5 (agentic pipeline).

Entry point: ``await build(db, ctx, cycle_id, tracer, settings)`` → PDF bytes or None.

Phase 1 (deterministic):
  Population fetch → metric computation → validation → audit appendix.

Phase 2.5 (agentic, only when settings is not None):
  NarrativeGenerationAgent runs through AgentRuntime, calling
  BedrockInvokeTool to produce a NarrativeOutput. The narrative is
  injected into the PDF as generated — there is no claim extraction,
  claim verification, or faithfulness-scoring gate on the text.

  A Bedrock timeout or unparseable response falls back to the Phase 1
  PDF with no narrative. Never raises HTTP 500.

Returns (None, None, {}) when there is no population.
Returns (pdf_bytes, narrative_output, agent_snapshot) always.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.tenant_dependency import TenantContext
from app.models.iquest_engine_output import IquestEngineOutput
from app.repositories import reporting_relationship_repository as rr_repo

from . import pdf_builder, queries
from .agent_runtime import AgentRuntime, ToolRegistry
from .agent_tools import BedrockInvokeTool
from .metric_context import build_context
from .narrative_agent import NarrativeOutput, NarrativeParseError, NarrativeTimeoutError
from .report_agents import NarrativeGenerationAgent
from .tracer import ReportTracer
from .validator import validate_all

log = structlog.get_logger()

_ORG_WIDE_ROLES = frozenset({
    "SUPER_ADMIN", "PLATFORM_ADMIN", "TENANT_ADMIN",
    "CFO", "CHRO", "HR", "HRBP", "C_AND_B",
})


async def _subtree_user_ids(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    manager_user_id: uuid.UUID,
) -> list[uuid.UUID]:
    """BFS expansion of the reporting tree beneath manager_user_id."""
    visited: set[uuid.UUID] = set()
    queue = [manager_user_id]
    result: list[uuid.UUID] = []
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        reports = await rr_repo.report_ids(db, tenant_id, cycle_id, current)
        for r in reports:
            if r not in visited:
                result.append(r)
                queue.append(r)
    return result


async def build(
    db: AsyncSession,
    ctx: TenantContext,
    cycle_id: uuid.UUID | None = None,
    tracer: ReportTracer | None = None,
    settings: Any = None,
) -> tuple[bytes | None, NarrativeOutput | None, dict]:
    """Build and return (pdf_bytes, narrative_output, agent_snapshot).

    ``narrative_output`` — the generated NarrativeOutput, or None when
    Phase 2.5 wasn't active or Bedrock generation failed.
    ``agent_snapshot``   — AgentRuntime.snapshot() dict; empty dict when
    Phase 2.5 not active.

    Returns (None, None, {}) when there is no population to report on.
    """
    tenant_id = ctx.active_tenant_id
    is_org_wide = bool(ctx.role_profile.tenant_roles & _ORG_WIDE_ROLES)

    fiscal_year = await queries.resolve_fiscal_year(db, tenant_id)

    if is_org_wide:
        population = await queries.fetch_population(
            db, tenant_id, fiscal_year, caller_employee_id=None, is_org_wide=True,
        )
        caller_name = "Full Organisation"
    else:
        if not cycle_id:
            return None, None, {}

        subtree_ids = await _subtree_user_ids(db, tenant_id, cycle_id, ctx.user.id)
        if not subtree_ids:
            return None, None, {}

        emp_id_rows = await db.execute(
            select(IquestEngineOutput.employee_id).where(
                IquestEngineOutput.tenant_id == tenant_id,
                IquestEngineOutput.subject_user_id.in_(subtree_ids),
                IquestEngineOutput.employee_id.isnot(None),
            )
        )
        emp_ids = [r for r in emp_id_rows.scalars().all() if r]
        if not emp_ids:
            return None, None, {}

        population = await queries.fetch_population_by_emp_ids(
            db, tenant_id, fiscal_year, emp_ids,
        )
        caller_name = f"{ctx.user.first_name} {ctx.user.last_name or ''}".strip()

    if not population:
        return None, None, {}

    # --- Phase 1: deterministic metrics ---
    data = await queries.build_report_data(
        db, tenant_id, fiscal_year, population, caller_name, is_org_wide,
        tracer=tracer,
    )

    phase1_validations = validate_all(tracer._metrics) if tracer else []
    snapshot = tracer.snapshot() if tracer else {}
    phase1_audit = {
        **snapshot,
        "withheld_sections": [name for name, _ in data.withheld_sections],
        "validations": [
            {
                "metric_id": v.metric_id,
                "status": v.status,
                "expected": str(v.expected_value) if v.expected_value is not None else None,
                "notes": v.notes,
            }
            for v in phase1_validations
        ],
    }

    # --- Phase 2.5: agentic narrative generation ---
    narr_out: NarrativeOutput | None = None
    narratives: dict | None = None
    agent_snapshot: dict = {}

    if settings is not None and tracer is not None:
        run_id = tracer.run_id
        ctx_contract = build_context(
            report_id=run_id,
            trace_id=run_id,
            tenant_id=tenant_id,
            data=data,
            metric_records=tracer._metrics,
        )

        # Wire tools
        tool_registry = ToolRegistry()
        tool_registry.register("bedrock_invoke", BedrockInvokeTool(settings))

        pipeline_run_id = uuid.uuid4()
        runtime = AgentRuntime(pipeline_run_id, run_id, tool_registry)
        narr_agent = NarrativeGenerationAgent(settings)

        log.info("report.agentic.attempt", trace_id=str(run_id))
        try:
            narr_result = await runtime.run_agent(
                narr_agent, {"ctx": ctx_contract, "attempt": 1}
            )
            narr_out = narr_result["narr_out"]
            narratives = {
                k: v.text
                for k, v in narr_out.sections.items()
                if v.status == "GENERATED" and v.text
            }
        except (NarrativeTimeoutError, NarrativeParseError) as exc:
            log.warning("report.agentic.blocked", reason=str(exc))
            narr_out = None
            narratives = None

        agent_snapshot = runtime.snapshot()

    pdf_bytes = pdf_builder.build_pdf(
        data,
        audit_data=phase1_audit if tracer else None,
        narratives=narratives,
        narrative_output=narr_out,
        agent_snapshot=agent_snapshot if agent_snapshot else None,
    )

    return pdf_bytes, narr_out, agent_snapshot
