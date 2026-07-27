"""iQuest AI streaming service.

Single responsibility: bridge the AI backend's streaming API to an
asyncio-friendly async token generator, build the JVRE rationale prompt,
and persist the completed text via the rationale repository. SSE framing
is left to the caller (router) — this module only ever yields raw tokens.

Both backends (Bedrock LLM and Ollama SLM) use the same thread+queue bridge
pattern: a daemon thread calls the synchronous SDK/HTTP client and puts raw
chunk bytes onto an asyncio.Queue; the async generator drains the queue
without ever blocking the event loop.

To switch backends, comment/uncomment the two blocks inside _token_stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import uuid
from collections.abc import AsyncIterator
from typing import Any

import boto3
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts import PAY_RATIONALE_SYSTEM, RATIONALE_DOMAIN_CONTEXT, _pct_from_bases
from app.core.config import Settings
from app.models.iquest_engine_output import IquestEngineOutput
from app.services import jvre_workspace_service
from app.services.bedrock_client import get_bedrock_client

logger = logging.getLogger(__name__)

_APP_JSON = "application/json"


# ---------------------------------------------------------------------------
# Sync stream workers (each runs in a daemon thread)
# ---------------------------------------------------------------------------

def _run_bedrock_stream(
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue[bytes | None],
    region: str,
    model_id: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    aws_access_key_id: str | None = None,
    aws_secret_access_key: str | None = None,
    aws_session_token: str | None = None,
) -> None:
    """Synchronous Bedrock streaming worker — runs in a daemon thread.

    Puts raw chunk bytes onto the asyncio queue; puts the ``None`` sentinel
    when done (or on error) so the async consumer knows the stream is finished.
    """
    client = boto3.client(
        "bedrock-runtime",
        region_name=region,
        aws_access_key_id=aws_access_key_id or None,
        aws_secret_access_key=aws_secret_access_key or None,
        aws_session_token=aws_session_token or None,
    )
    body = json.dumps({
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    })
    try:
        resp = client.invoke_model_with_response_stream(
            modelId=model_id,
            body=body,
            contentType=_APP_JSON,
            accept=_APP_JSON,
        )
        for event in resp["body"]:
            if "chunk" in event:
                loop.call_soon_threadsafe(queue.put_nowait, event["chunk"]["bytes"])
    except Exception:
        logger.exception("Bedrock stream error for model %s", model_id)
    finally:
        loop.call_soon_threadsafe(queue.put_nowait, None)


def _run_ollama_stream(
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue[bytes | None],
    base_url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    system: str | None = None,
) -> None:
    """Synchronous Ollama streaming worker — runs in a daemon thread.

    Mirrors _run_bedrock_stream exactly: puts raw token bytes onto the asyncio
    queue and sends the None sentinel when done so the consumer loop exits.
    Requires a local Ollama instance (https://ollama.com). Pull the model
    first: ``ollama pull <model>``.
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    if system:
        payload["system"] = system
    try:
        with contextlib.ExitStack() as stack:
            client = stack.enter_context(httpx.Client(base_url=base_url, timeout=120))
            resp = stack.enter_context(client.stream("POST", "/api/generate", json=payload))
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("response"):
                    loop.call_soon_threadsafe(
                        queue.put_nowait, data["response"].encode()
                    )
                if data.get("done"):
                    break
    except Exception:
        logger.exception("Ollama stream error for model %s", model)
    finally:
        loop.call_soon_threadsafe(queue.put_nowait, None)


# ---------------------------------------------------------------------------
# Chunk decoders
# ---------------------------------------------------------------------------

def _parse_bedrock_token(chunk_bytes: bytes) -> str:
    """Extract text delta from one Bedrock streaming chunk.

    Handles native Bedrock Claude format (``content_block_delta``) and the
    OpenAI-compatible endpoint format (``choices[0].delta.content``).
    Returns an empty string for chunks that carry no text (e.g. start/stop
    events) so callers can filter with a simple truthiness check.
    """
    try:
        data = json.loads(chunk_bytes)
        if data.get("type") == "content_block_delta":
            return data.get("delta", {}).get("text") or ""
        return (data.get("choices") or [{}])[0].get("delta", {}).get("content") or ""
    except (json.JSONDecodeError, KeyError, IndexError):
        return ""


def _parse_ollama_token(chunk_bytes: bytes) -> str:
    """Decode a raw Ollama token put by _run_ollama_stream."""
    return chunk_bytes.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _fmt(val: object, prefix: str = "", suffix: str = "") -> str:
    """Format a nullable value with optional pre/suffix; returns 'N/A' for None."""
    return f"{prefix}{val}{suffix}" if val is not None else "N/A"


# ---------------------------------------------------------------------------
# Bedrock helpers (also used by iquest_ai_router for non-streaming calls)
# ---------------------------------------------------------------------------

def invoke_bedrock_sync(
    settings: Any,
    messages: list[dict[str, str]],
    max_tokens: int = 400,
    temperature: float = 0.4,
) -> str:
    """Synchronous (non-streaming) Bedrock call. Must run in a thread pool."""
    client = get_bedrock_client(settings)
    body = json.dumps({"messages": messages, "max_tokens": max_tokens, "temperature": temperature})
    resp = client.invoke_model(
        modelId=settings.bedrock_model_id,
        body=body,
        contentType=_APP_JSON,
        accept=_APP_JSON,
    )
    result = json.loads(resp["body"].read())
    return (result.get("choices") or [{}])[0].get("message", {}).get("content", "")


def invoke_llm_sync(
    settings: Any,
    messages: list[dict[str, str]],
    max_tokens: int = 400,
    temperature: float = 0.4,
) -> str:
    """Synchronous, non-streaming LLM call — used for suggested-question generation.

    To switch backends: comment out the active block below and uncomment the
    other, mirroring the pattern in ``_token_stream``. Restart the server —
    no other changes needed.
    """
    # --- LLM: AWS Bedrock ---
    # return invoke_bedrock_sync(settings, messages, max_tokens, temperature)

    # --- SLM: Ollama (local) — comment out Bedrock block above, uncomment below ---
    prompt = messages[-1]["content"] if messages else ""
    with httpx.Client(base_url=settings.ollama_base_url, timeout=120) as client:
        resp = client.post("/api/generate", json={
            "model": settings.ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        })
        return resp.json().get("response", "")


async def stream_scope_response(settings: Any, prompt: str) -> AsyncIterator[str]:
    """Async generator that streams tokens for BUDGET/GLOBAL scope Q&A.

    Routes to Bedrock (chunked sync) or Ollama (native stream) based on
    ``settings.ai_provider``.
    """
    if getattr(settings, "ai_provider", "ollama") == "bedrock":
        raw = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: invoke_bedrock_sync(
                settings,
                [{"role": "user", "content": prompt}],
                max_tokens=800,
            ),
        )
        chunk_size = 40
        for i in range(0, len(raw), chunk_size):
            yield raw[i : i + chunk_size]
    else:
        from app.services.compchat import slm

        async for token in slm.stream_generate(
            settings.ollama_base_url,
            settings.ollama_model,
            prompt,
            temperature=0.3,
            max_tokens=800,
        ):
            yield token


def build_prompt(eng: IquestEngineOutput) -> tuple[str, str]:
    """Return (system, user_prompt) for the JVRE rationale request.

    Separating the format rules into the system message improves compliance
    on local SLMs (Ollama) that treat system vs user content differently.
    The iquest_context.md knowledge base goes in the user message so it is
    part of the task framing, not the constraint layer.
    """
    # Qualitative market position so the narrator never quotes a raw index/ratio.
    try:
        _cr = float(eng.external_cr) if eng.external_cr is not None else None
    except (TypeError, ValueError):
        _cr = None
    if _cr is None:
        _market_words = "not available"
    elif _cr < 0.95:
        _market_words = "paid BELOW the market rate for the role (a market-correction case)"
    elif _cr > 1.10:
        _market_words = "paid ABOVE the market rate (favour a modest, performance-led increase)"
    else:
        _market_words = "paid in line with the market rate"

    user_prompt = (
        RATIONALE_DOMAIN_CONTEXT
        + f"""

---

TASK: Write a compensation rationale for the employee below. Cover:
1. What salary change is recommended, the amount, and the primary reason (promotion / market alignment / performance recognition / tenure).
2. Why this is the right time — reference performance level, time since the last increase, and how current pay compares to market (without using P25/P50/P75 or compa-ratio labels). Include retention and equity considerations — vesting timeline, role replacement difficulty, what happens if we do nothing.
3. (Optional) Any forward-looking context — what this positions the employee for, or constraints to note.

=== EMPLOYEE DATA ===
Name: {_fmt(eng.employee_name)} | Role: {_fmt(eng.job_role)} | Band: {_fmt(eng.band)} | Dept: {_fmt(eng.department)} | Location: {_fmt(eng.location)}
Supervisor: {_fmt(eng.supervisor)} | Tenure: {_fmt(eng.tenure_years, suffix=" yrs")} | Joined: {_fmt(eng.doj)}

Performance:
- Performance Band: {_fmt(eng.rating_band)} | Growth Potential: {_fmt(eng.potential_rating)}/5
- Role Difficulty to Replace (1-10): {_fmt(eng.criticality_score)} | Manager Assessment: {_fmt(eng.manager_criticality_score)}/10
- Time to Fill If Vacant: {_fmt(eng.ttf_months, suffix=" months")} | Estimated Replacement Cost: ₹{_fmt(eng.cost_of_replacement_inr)}
- Overall Retention Risk Score: {_fmt(eng.jvre_score)}/10 | Exit Risk Signal: {_fmt(eng.exit_risk_signal)}

Current Pay:
- Base Salary: ₹{_fmt(eng.current_base_inr)} | Bonus Target: {_fmt(eng.target_bonus_pct, suffix="%")} | Total Annual Cash: ₹{_fmt(eng.total_cash_inr)}
- Market position: {_market_words} | Months Since Last Increase: {_fmt(eng.months_since_last_increase)}

Market Pay Range for This Role ({_fmt(eng.benchmark_family)}):
- Lower: ₹{_fmt(eng.benchmark_p25)} | Midpoint: ₹{_fmt(eng.benchmark_p50)} | Upper: ₹{_fmt(eng.benchmark_p75)}

Equity:
- Unvested Value: ${_fmt(eng.unvested_usd)} USD (₹{_fmt(eng.equity_value_inr)} INR)
- Next Vesting Date: {_fmt(eng.next_vest_date)} ({_fmt(eng.months_to_next_vest, suffix=" months away")})

Recommendation:
- New Base Salary: ₹{_fmt(eng.rec_new_base_inr)} | Increase: {_pct_from_bases(eng.current_base_inr, eng.rec_new_base_inr)} | New Total Cash: ₹{_fmt(eng.rec_total_cash_inr)}

Active Signals: Promotion in scope={_fmt(eng.promotion_flag)} | No increase in multiple cycles={_fmt(eng.multi_cycle_flag)} | Budget may be constrained={_fmt(eng.funding_gap_flag)} | Near band ceiling={_fmt(eng.band_ceiling_flag)}

=== FINAL OUTPUT CONSTRAINTS (these override the knowledge base) ===
- Start immediately with the first sentence of the rationale — NO title, heading, label, or blank line first.
- Plain business language only. NEVER write the words "compa-ratio", "retention risk score", "JVRE", "percentile", "P25", "P50", "P75", or any "x/10" score. Translate them into plain terms.
- Use the "Market position" line above for direction: if it says ABOVE market, justify a modest, performance-led increase; if BELOW market, justify a market correction; if in line, frame it as maintaining alignment. Do not state this backwards.
- ONLY describe this as a promotion or level change if "Promotion in scope" is True. If it is False, treat it as a within-band adjustment and never claim the employee is being promoted.
- Do not cite equity, vesting, or replacement-cost figures that are shown as N/A — omit them instead of inventing values.
- 2-3 short paragraphs, 90-150 words total. Bold only 1-3 key items. State currency amounts with correct thousands-comma grouping. End with a complete, forward-looking sentence.

=== RATIONALE ==="""
    )
    return PAY_RATIONALE_SYSTEM, user_prompt


# ---------------------------------------------------------------------------
# Token stream — swap backend here
# ---------------------------------------------------------------------------

async def _token_stream(settings: Settings, system: str, prompt: str) -> AsyncIterator[str]:
    """Yield decoded text tokens from the active AI backend.

    To switch between LLM (Bedrock) and local SLM (Ollama):
      1. Comment out the active block below and uncomment the other.
      2. Restart the server — no other changes needed.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    # --- LLM: AWS Bedrock ---
    # threading.Thread(
    #     target=_run_bedrock_stream,
    #     args=(
    #         loop, queue,
    #         settings.aws_region,
    #         settings.bedrock_model_id,
    #         system + "\n\n" + prompt,  # Bedrock: prepend system to user message
    #         settings.bedrock_max_tokens,
    #         settings.bedrock_temperature,
    #         settings.aws_access_key_id,
    #         settings.aws_secret_access_key,
    #         settings.aws_session_token,
    #     ),
    #     daemon=True,
    # ).start()
    # parse_token = _parse_bedrock_token

    # --- SLM: Ollama (local) — comment out Bedrock block above, uncomment below ---
    threading.Thread(
        target=_run_ollama_stream,
        args=(
            loop, queue,
            settings.ollama_base_url,
            settings.ollama_model,
            prompt,
            settings.bedrock_max_tokens,
            settings.bedrock_temperature,
            system,
        ),
        daemon=True,
    ).start()
    parse_token = _parse_ollama_token

    while True:
        chunk_bytes = await queue.get()
        if chunk_bytes is None:
            break
        token = parse_token(chunk_bytes)
        if token:
            yield token


# ---------------------------------------------------------------------------
# Token stream — persists the rationale once fully generated
# ---------------------------------------------------------------------------

async def stream_pay_rationale_tokens(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cycle_id: uuid.UUID,
    subject_user_id: uuid.UUID,
    eng: IquestEngineOutput,
    settings: Settings,
) -> AsyncIterator[str]:
    """Yield raw rationale text tokens, then persist the completed rationale.

    The active backend is selected by the commented/uncommented blocks in
    ``_token_stream``. SSE framing is the caller's responsibility (see
    ``stream_scope_response`` for the same convention).
    """
    tokens: list[str] = []
    system, user_prompt = build_prompt(eng)
    async for token in _token_stream(settings, system, user_prompt):
        tokens.append(token)
        yield token

    full_text = "".join(tokens)
    if full_text:
        try:
            await jvre_workspace_service.persist_generated_rationale(
                db, tenant_id, cycle_id, subject_user_id,
                full_text, settings.bedrock_model_id,
            )
        except Exception:
            logger.exception(
                "Failed to persist rationale for subject %s in cycle %s",
                subject_user_id, cycle_id,
            )
