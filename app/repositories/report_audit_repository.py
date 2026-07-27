"""Persistence layer for the report generation logging tables.

The compensation report PDF itself is generated and streamed straight
back to the caller — no per-run audit trail (steps, dataset provenance,
metric values, validation results, run/manifest rows) is persisted.

What *is* still persisted here is pipeline observability/logging for
the AI stages: one NarrativeGeneration row per Bedrock call, and the
agent/tool execution log for the Phase 2.5 agentic pipeline. Both are
grouped by a single ``run_id`` UUID minted per request (not backed by
a ``report_runs`` table — it's a plain correlation id).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_audit import (
    AgentPipelineRun,
    AgentRunLog as AgentRunLogORM,
    ToolRunLog as ToolRunLogORM,
)
from app.models.report_narrative import NarrativeGeneration
from app.services.compchat.reporting.narrative_agent import NarrativeOutput


# ---------------------------------------------------------------------------
# Phase 2 — narrative audit flush
# ---------------------------------------------------------------------------

async def flush_narrative(
    db: AsyncSession,
    run_id: uuid.UUID,
    narr_out: NarrativeOutput,
    ctx_payload: dict,
) -> uuid.UUID:
    """Persist one narrative generation attempt.

    Returns the NarrativeGeneration.id. Caller owns the commit.
    """
    gen_id = uuid.uuid4()
    parsed_sections = {
        k: {
            "status": v.status,
            "text": v.text,
            "metric_ids_cited": v.metric_ids_cited,
            "word_count": v.word_count,
        }
        for k, v in narr_out.sections.items()
    }

    db.add(NarrativeGeneration(
        id=gen_id,
        trace_id=run_id,
        run_id=run_id,
        contract_id=uuid.UUID(str(ctx_payload.get("contract_id", uuid.uuid4()))),
        attempt=1,
        prompt_version=narr_out.prompt_version,
        model_id=narr_out.model_id,
        status="COMPLETED",
        narrative_status="GENERATED",
        context_payload=ctx_payload,
        raw_output=narr_out.raw_output,
        parsed_sections=parsed_sections,
        input_tokens=narr_out.input_tokens,
        output_tokens=narr_out.output_tokens,
        latency_ms=narr_out.latency_ms,
    ))
    await db.flush()
    return gen_id


# ---------------------------------------------------------------------------
# Phase 2.5 — agent pipeline audit flush
# ---------------------------------------------------------------------------

async def flush_agent_pipeline(
    db: AsyncSession,
    run_id: uuid.UUID,
    agent_snapshot: dict,
) -> uuid.UUID:
    """Persist all agent and tool audit records for one pipeline run.

    agent_snapshot is AgentRuntime.snapshot():
      pipeline_run_id, trace_id, agent_logs (list[AgentRunLog]), tool_logs (list[ToolRunLog])

    Returns the AgentPipelineRun.id. Caller owns the commit.
    """
    from app.services.compchat.reporting.agent_runtime import AgentRunLog, ToolRunLog

    pipeline_run_id: uuid.UUID = agent_snapshot["pipeline_run_id"]
    agent_logs: list[AgentRunLog] = agent_snapshot.get("agent_logs", [])
    tool_logs: list[ToolRunLog] = agent_snapshot.get("tool_logs", [])

    total_duration_ms: int | None = None
    if agent_logs:
        durations = [a.duration_ms for a in agent_logs if a.duration_ms is not None]
        if durations:
            total_duration_ms = sum(durations)

    db.add(AgentPipelineRun(
        id=pipeline_run_id,
        trace_id=run_id,
        run_id=run_id,
        pipeline_version="2.5.0",
        status="COMPLETED",
        agent_count=len(agent_logs),
        tool_call_count=len(tool_logs),
        total_duration_ms=total_duration_ms,
    ))
    await db.flush()

    # Agent run logs
    for a in agent_logs:
        db.add(AgentRunLogORM(
            id=a.agent_run_id,
            pipeline_run_id=pipeline_run_id,
            trace_id=run_id,
            run_id=run_id,
            agent_name=a.agent_name,
            execution_order=a.execution_order,
            status=a.status,
            input_summary=a.input_summary,
            output_summary=a.output_summary,
            error_message=a.error_message,
            started_at=a.started_at,
            completed_at=a.completed_at,
            duration_ms=a.duration_ms,
        ))
    await db.flush()

    # Tool run logs (FK to agent_run_logs, so flush agent logs first)
    for t in tool_logs:
        db.add(ToolRunLogORM(
            id=t.tool_run_id,
            pipeline_run_id=pipeline_run_id,
            agent_run_id=t.agent_run_id,
            trace_id=run_id,
            run_id=run_id,
            tool_name=t.tool_name,
            status=t.status,
            input_repr=t.input_repr,
            output_repr=t.output_repr,
            error_message=t.error_message,
            started_at=t.started_at,
            completed_at=t.completed_at,
            duration_ms=t.duration_ms,
        ))

    await db.flush()
    return pipeline_run_id
