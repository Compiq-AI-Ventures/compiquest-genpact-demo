"""iQuest AI — suggested-question pill generation and multi-turn Q&A.

Two endpoints:
  POST /ai/suggested-questions  — non-streaming; returns 4 contextual pills
  POST /ai/iquest-query         — SSE streaming; answers Q via the CompChat
                                  11-layer pipeline (see app.services.compchat)

``/suggested-questions`` calls ``iquest_streaming_service.invoke_llm_sync`` for
pill generation via AWS Bedrock.
``/iquest-query`` no longer dumps the whole record into a prompt — it runs
the grounded, RBAC-gated, deterministic-tool CompChat pipeline so the model
narrates only pre-validated facts and never invents numbers.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import prompts
from app.core.config import get_settings
from app.dependencies.scoped_db_dependency import get_tenant_scoped_db
from app.dependencies.tenant_dependency import TenantContext, get_tenant_context
from app.models.iquest_engine_output import IquestEngineOutput
from app.services import jvre_workspace_service
from app.services.compchat import pipeline as compchat_pipeline
from app.services.iquest_context_service import build_budget_context, build_global_context
from app.services.iquest_streaming_service import invoke_llm_sync, stream_scope_response

router = APIRouter(prefix="/ai", tags=["iquest-ai"])

_APP_JSON = "application/json"


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------


class SuggestedQuestionsRequest(BaseModel):
    cycle_id: uuid.UUID
    subject_user_id: uuid.UUID | None = None
    rationale_text: str = ""
    scope: Literal["PAY", "BUDGET", "GLOBAL"] = "PAY"
    entity_id: uuid.UUID | None = None


class ChatMessage(BaseModel):
    role: str
    content: str


class IQuestQueryRequest(BaseModel):
    cycle_id: uuid.UUID
    subject_user_id: uuid.UUID | None = None
    rationale_text: str = ""
    messages: list[ChatMessage]
    scope: Literal["PAY", "BUDGET", "GLOBAL"] = "PAY"
    entity_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# Shared context helpers
# ---------------------------------------------------------------------------


async def _load_engine_output(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    subject_user_id: uuid.UUID,
) -> IquestEngineOutput:
    result = await db.execute(
        select(IquestEngineOutput).where(
            IquestEngineOutput.tenant_id == tenant_id,
            IquestEngineOutput.cycle_id == cycle_id,
            IquestEngineOutput.subject_user_id == subject_user_id,
        )
    )
    eng = result.scalar_one_or_none()
    if eng is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="iQuest engine output not found for this subject.",
        )
    return eng


async def _buffered_sse_stream(token_stream, chunk_size: int = 24):
    """Buffer a raw token stream fully, strip any leaked ReAct scaffold, then
    re-emit as SSE ``data:`` frames.

    Local SLMs don't reliably keep the ReAct scratchpad silent — a raw
    token-by-token pass-through would print a leaked "## Answer / ##
    Reasoning" scaffold live as it generates, and by the time it's visible
    there's no way to retract it. Buffering trades a small delay before the
    first frame for a guarantee that only the clean final answer is ever
    shown, still re-chunked so the UI gets its typewriter effect.
    """
    buffer_parts: list[str] = []
    async for token in token_stream:
        buffer_parts.append(token)
    answer = prompts.strip_react_scaffold("".join(buffer_parts).strip())
    for i in range(0, len(answer), chunk_size):
        yield f"data: {json.dumps({'token': answer[i : i + chunk_size]})}\n\n"
    yield "data: [DONE]\n\n"


def _parse_questions_from_llm(raw: str) -> list[str]:
    """Parse a JSON array of question strings from a raw LLM response.

    Strips markdown code fences, attempts JSON parse, falls back to
    line-scanning for lines that end with '?' if JSON is malformed.
    """
    raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        questions = json.loads(raw)
        if not isinstance(questions, list):
            raise ValueError
        return [str(q) for q in questions[:4]]
    except (json.JSONDecodeError, ValueError):
        return [
            line.strip().strip('"').strip("'")
            for line in raw.splitlines()
            if line.strip().endswith("?")
        ][:4]


# ---------------------------------------------------------------------------
# Endpoint 1: Budget allocation rationale (streaming SSE)
# ---------------------------------------------------------------------------


@router.get(
    "/budget-rationale/{cycle_id}/{manager_user_id}",
    summary="Stream a budget allocation rationale narrative (SSE)",
    responses={
        401: {"description": "Missing or invalid Bearer token"},
        404: {"description": "No budget allocation found for this manager"},
    },
)
async def stream_budget_rationale(
    cycle_id: uuid.UUID,
    manager_user_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> StreamingResponse:
    # Allow the manager to view their own rationale; otherwise require that
    # the manager is in the caller's reporting chain (MoM viewing a sub-manager).
    if ctx.user.id != manager_user_id:
        await jvre_workspace_service.assert_subject_in_reporting_chain(
            db, ctx.active_tenant_id, ctx.user.id, manager_user_id
        )

    settings = get_settings()
    context_block = await build_budget_context(db, ctx.active_tenant_id, cycle_id, manager_user_id)

    full_prompt = (
        prompts.BUDGET_RATIONALE_SYSTEM
        + "\n\n---\n\n"
        + context_block
        + "\n\nWrite the budget allocation rationale:"
    )

    token_stream = stream_scope_response(settings, full_prompt)

    return StreamingResponse(
        _buffered_sse_stream(token_stream),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Endpoint 1b: GLOBAL cycle overview stream (SSE)
# ---------------------------------------------------------------------------


@router.get(
    "/global-rationale/{cycle_id}",
    summary="Stream an org-wide cycle overview narrative (SSE)",
    responses={
        401: {"description": "Missing or invalid Bearer token"},
    },
)
async def stream_global_rationale(
    cycle_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> StreamingResponse:
    """Auto-generated "Cycle Overview" card for the GLOBAL-scope iQuest panel —
    the aggregate-only counterpart to budget/pay rationale, for CFO/CHRO/HR
    users opening the assistant from the navbar (no specific subject/manager)."""
    settings = get_settings()
    context_block = await build_global_context(db, ctx.active_tenant_id, cycle_id)

    full_prompt = (
        prompts.GLOBAL_OVERVIEW_SYSTEM
        + "\n\n---\n\n"
        + context_block
        + "\n\nWrite the cycle overview:"
    )

    token_stream = stream_scope_response(settings, full_prompt)

    return StreamingResponse(
        _buffered_sse_stream(token_stream),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Endpoint 2: PAY rationale stream (SSE)
# ---------------------------------------------------------------------------


@router.get(
    "/pay-rationale/{cycle_id}/{subject_user_id}",
    summary="Stream a JVRE compensation rationale for one subject (SSE)",
    responses={
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Subject not in caller's reporting chain"},
        404: {"description": "No JVRE snapshot for this subject in this cycle"},
    },
)
async def stream_pay_rationale(
    cycle_id: uuid.UUID,
    subject_user_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> StreamingResponse:
    """Stream a compensation rationale for a single IC (PAY scope).

    Access control: calling user must be in the subject's manager chain.
    Delegates all prompt-building and streaming logic to iquest_streaming_service.
    """
    from app.models.iquest_engine_output import IquestEngineOutput
    from app.services import iquest_streaming_service

    await jvre_workspace_service.assert_subject_in_reporting_chain(
        db, ctx.active_tenant_id, ctx.user.id, subject_user_id
    )

    settings = get_settings()

    result = await db.execute(
        select(IquestEngineOutput).where(
            IquestEngineOutput.tenant_id == ctx.active_tenant_id,
            IquestEngineOutput.cycle_id == cycle_id,
            IquestEngineOutput.subject_user_id == subject_user_id,
        )
    )
    eng = result.scalar_one_or_none()
    if eng is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="iQuest engine output not found for this subject in this cycle.",
        )

    eng = await jvre_workspace_service.sync_engine_output(db, eng)

    token_stream = iquest_streaming_service.stream_pay_rationale_tokens(
        db, ctx.active_tenant_id, cycle_id, subject_user_id, eng, settings
    )

    async def event_stream():
        async for token in token_stream:
            yield f"data: {json.dumps({'token': token})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Endpoint 3: Suggested questions (non-streaming)
# ---------------------------------------------------------------------------


@router.post(
    "/suggested-questions",
    summary="Generate 4 contextual suggested questions (scope-aware: PAY / BUDGET / GLOBAL)",
    responses={
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Subject not in caller's reporting chain"},
        404: {"description": "iQuest engine output not found for this subject"},
    },
)
async def get_suggested_questions(
    body: SuggestedQuestionsRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    settings = get_settings()
    scope = body.scope

    # --- BUDGET / GLOBAL: generate questions from the scope context block ---
    if scope in ("BUDGET", "GLOBAL"):
        if scope == "BUDGET":
            if not body.entity_id:
                raise HTTPException(status_code=422, detail="entity_id (manager UUID) required for BUDGET scope")
            context_block = await build_budget_context(db, ctx.active_tenant_id, body.cycle_id, body.entity_id)
            prompt = prompts.build_budget_questions_prompt(context_block, body.rationale_text)
        else:
            context_block = await build_global_context(db, ctx.active_tenant_id, body.cycle_id)
            prompt = prompts.build_global_questions_prompt(context_block)

        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(
            None,
            lambda: invoke_llm_sync(settings, [{"role": "user", "content": prompt}], max_tokens=400, temperature=0.4),
        )
        return {"questions": _parse_questions_from_llm(raw)}

    # --- PAY scope ---
    if not body.subject_user_id:
        raise HTTPException(status_code=422, detail="subject_user_id required for PAY scope")
    await jvre_workspace_service.assert_subject_in_reporting_chain(
        db, ctx.active_tenant_id, ctx.user.id, body.subject_user_id
    )
    eng = await _load_engine_output(db, ctx.active_tenant_id, body.cycle_id, body.subject_user_id)

    prompt = prompts.build_pay_questions_prompt(eng, body.rationale_text)
    loop = asyncio.get_running_loop()
    raw = await loop.run_in_executor(
        None,
        lambda: invoke_llm_sync(settings, [{"role": "user", "content": prompt}], max_tokens=400, temperature=0.4),
    )
    return {"questions": _parse_questions_from_llm(raw)}


# ---------------------------------------------------------------------------
# Endpoint 2: iQuest Q&A (streaming SSE)
# ---------------------------------------------------------------------------


@router.post(
    "/iquest-query",
    summary="Stream a grounded answer via the CompChat 11-layer pipeline (SSE)",
    responses={
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Tenant context required"},
    },
)
async def iquest_query_stream(
    body: IQuestQueryRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> StreamingResponse:
    """Answer a manager's question — scope-aware.

    * scope=PAY (default): routes through the full CompChat 11-layer pipeline,
      anchored to a specific IC (subject_user_id). Access control, intent
      classification, tool selection, grounding and audit trace all happen
      inside ``compchat.pipeline``.
    * scope=BUDGET: loads the calling manager's budget allocation + team
      context and streams a direct LLM answer without IC-level resolution.
    * scope=GLOBAL: loads org-wide cycle aggregates and streams a direct
      LLM answer scoped to aggregate-only data (no individual IC details).
    """
    settings = get_settings()
    scope = body.scope

    # --- BUDGET / GLOBAL: build context snapshot, stream directly ---
    if scope in ("BUDGET", "GLOBAL"):
        last_user_idx = next(
            (i for i in range(len(body.messages) - 1, -1, -1) if body.messages[i].role == "user"),
            None,
        )
        question = body.messages[last_user_idx].content if last_user_idx is not None else ""

        if scope == "BUDGET":
            if not body.entity_id:
                raise HTTPException(status_code=422, detail="entity_id (manager UUID) required for BUDGET scope")
            context_block = await build_budget_context(db, ctx.active_tenant_id, body.cycle_id, body.entity_id)
        else:
            context_block = await build_global_context(db, ctx.active_tenant_id, body.cycle_id)

        system_prompt = prompts.build_scope_chat_system_prompt(scope, context_block)
        history_text = "\n".join(
            f"{m.role.upper()}: {m.content}"
            for m in body.messages
        ) if len(body.messages) > 1 else ""
        full_prompt = (
            system_prompt
            + ("\n\n## Conversation so far\n" + history_text if history_text else "")
            + f"\n\nUSER: {question}\n\nASSISTANT:"
        )

        token_stream = stream_scope_response(settings, full_prompt)

        return StreamingResponse(
            _buffered_sse_stream(token_stream),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # --- PAY scope (existing compchat path, unchanged) ---
    if not body.subject_user_id:
        raise HTTPException(status_code=422, detail="subject_user_id required for PAY scope")

    last_user_idx = next(
        (i for i in range(len(body.messages) - 1, -1, -1) if body.messages[i].role == "user"),
        None,
    )
    question = body.messages[last_user_idx].content if last_user_idx is not None else ""
    history = [
        (m.role, m.content)
        for i, m in enumerate(body.messages)
        if i != last_user_idx
    ]

    result = await compchat_pipeline.prepare(
        db,
        ctx,
        settings,
        question=question,
        subject_user_id=body.subject_user_id,
        cycle_id=body.cycle_id,
        fiscal_year_hint=None,
        rationale_text=body.rationale_text,
        history=history,
        # The chat panel is anchored to the open recommendation: keep it
        # on this subject and serve peer questions as aggregates rather
        # than disclosing another named individual's pay.
        allow_entity_switch=False,
    )

    async def event_stream():
        async for chunk in compchat_pipeline.narrate(db, ctx, settings, result):
            yield f"data: {json.dumps({'token': chunk})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-CompChat-Trace-Id": result.trace_id,
        },
    )


# ---------------------------------------------------------------------------
# Endpoint 3: Compensation PDF report
# ---------------------------------------------------------------------------


@router.get(
    "/reports/compensation.pdf",
    summary="Download a scoped compensation PDF report",
    response_class=Response,
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "Seven-section compensation report scoped to the caller's population",
        },
        404: {"description": "No compensation data found for this population"},
    },
)
async def download_compensation_report(
    cycle_id: uuid.UUID | None = Query(default=None, description="Cycle context for manager-role scoping"),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> Response:
    """Generate and return a PDF compensation report.

    Org-wide roles (CFO, CHRO, HR, HRBP, C&B, TENANT_ADMIN) receive the
    full organisation report. MANAGER and MANAGER_OF_MANAGERS receive a
    report scoped to their reporting subtree for the given cycle.

    The report is generated and streamed straight back — no per-run
    audit trail (steps, dataset provenance, metric values, validation
    results) is persisted. The ``X-Report-Trace-Id`` response header
    carries a UUID that groups this request's narrative-generation and
    agent-pipeline log rows.
    """
    from app.repositories import report_audit_repository as audit_repo
    from app.services.compchat import reporting as report_builder
    from app.services.compchat.reporting.tracer import ReportTracer

    run_id = uuid.uuid4()
    tracer = ReportTracer(trace_id=run_id, run_id=run_id)
    settings = get_settings()

    pdf_bytes, narr_out, agent_snapshot = await report_builder.build(
        db, ctx, cycle_id=cycle_id, tracer=tracer, settings=settings,
    )

    if pdf_bytes is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No compensation data found for your population. "
                "For manager roles, a valid cycle_id is required."
            ),
        )

    # Flush Phase 2 narrative audit record
    narrative_status = "NONE"
    if narr_out is not None:
        ctx_payload = {}
        await audit_repo.flush_narrative(db, run_id, narr_out, ctx_payload)
        narrative_status = "GENERATED"

    # Flush Phase 2.5 agent pipeline audit records
    if agent_snapshot:
        await audit_repo.flush_agent_pipeline(db, run_id, agent_snapshot)

    await db.commit()

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=compensation_report.pdf",
            "X-Report-Trace-Id": str(run_id),
            "X-Report-Status": "COMPLETED",
            "X-Report-Narrative-Status": narrative_status,
        },
    )


# ---------------------------------------------------------------------------
# Endpoint 4: CD&A (Compensation Discussion & Analysis) report from upload
# ---------------------------------------------------------------------------

# 10 MB ceiling — a compensation-table file is a few KB; anything larger is
# almost certainly not the expected input.
_CDA_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_CDA_ACCEPTED_EXTS = (".xlsx", ".xlsm", ".docx", ".pdf")
# The report is delivered as a Word document (.docx).
_CDA_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


@router.post(
    "/reports/cda",
    summary="Generate a Compensation Discussion & Analysis (CD&A) Word document from an uploaded file",
    response_class=Response,
    responses={
        200: {
            "content": {_CDA_MEDIA_TYPE: {}},
            "description": "The generated CD&A report (.docx), laid out per the Genpact template",
        },
        401: {"description": "Missing or invalid Bearer token"},
        413: {"description": "Uploaded file exceeds the size limit"},
        422: {"description": "The uploaded file could not be parsed into report data"},
    },
)
async def generate_cda_report(
    file: UploadFile = File(..., description="Executive compensation table (.xlsx, .docx, or .pdf)"),
    ctx: TenantContext = Depends(get_tenant_context),
) -> Response:
    """Generate a CD&A report (.docx) from an uploaded compensation file.

    Accepts an **.xlsx/.xlsm workbook, a .docx document, or a .pdf** carrying a
    per-executive compensation table (Executive, Base Salary, Annual Bonus,
    PSU, RSU). The parser detects the format by magic bytes and extracts every
    figure deterministically from ``file``; the report's wording is the fixed
    Genpact CD&A template and only the numbers in the compensation tables
    change per upload. No language model is involved.

    Auth-gated (any authenticated tenant user). No database access and no
    persisted state — the Word document is generated and streamed straight back.
    """
    from app.services import cda

    filename = file.filename or ""
    if not filename.lower().endswith(_CDA_ACCEPTED_EXTS):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Please upload an .xlsx, .docx, or .pdf file.",
        )

    content = await file.read()
    if len(content) > _CDA_MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Uploaded file is too large (limit 10 MB).",
        )

    try:
        docx_bytes = await cda.build_cda_report(content, filename=filename)
    except cda.CDAParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return Response(
        content=docx_bytes,
        media_type=_CDA_MEDIA_TYPE,
        headers={
            "Content-Disposition": 'attachment; filename="genpact_cda_report.docx"',
            "X-Report-Status": "COMPLETED",
        },
    )
