"""iQuest AI streaming service.

Single responsibility: call AWS Bedrock for the JVRE rationale and
BUDGET/GLOBAL scope Q&A, build the JVRE rationale prompt, and persist the
completed text via the rationale repository. Bedrock's ``invoke_model`` is
synchronous (boto3 has no async client), so every call runs in a thread-pool
executor via ``run_in_executor`` to avoid blocking the event loop. There is
no true token-by-token stream — the full completion is fetched in one call
and then chunked into an async generator so the SSE framing in the router
layer is unchanged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts import PAY_RATIONALE_SYSTEM, RATIONALE_DOMAIN_CONTEXT, _pct_from_bases
from app.core.config import Settings
from app.models.iquest_engine_output import IquestEngineOutput
from app.services import jvre_workspace_service
from app.services.bedrock_client import get_bedrock_client

logger = logging.getLogger(__name__)

_APP_JSON = "application/json"
_CHUNK_SIZE = 40


# ---------------------------------------------------------------------------
# Bedrock helpers
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
    """Synchronous, non-streaming LLM call — used for suggested-question
    generation and the P&L/C&B executive-summary bullets.
    """
    return invoke_bedrock_sync(settings, messages, max_tokens, temperature)


async def stream_scope_response(settings: Any, prompt: str) -> AsyncIterator[str]:
    """Async generator that yields chunked tokens for BUDGET/GLOBAL scope Q&A.

    Calls Bedrock once (non-streaming) then chunks the result — Bedrock has
    no true streaming here, this fakes a typewriter effect for the SSE layer.
    """
    raw = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: invoke_bedrock_sync(
            settings,
            [{"role": "user", "content": prompt}],
            max_tokens=800,
        ),
    )
    for i in range(0, len(raw), _CHUNK_SIZE):
        yield raw[i : i + _CHUNK_SIZE]


def build_prompt(eng: IquestEngineOutput) -> tuple[str, str]:
    """Return (system, user_prompt) for the JVRE rationale request."""
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
- 2-3 short paragraphs, 90-150 words total. Bold only 1-3 key items, using markdown **double-asterisks** around each bolded item. State currency amounts with correct thousands-comma grouping. End with a complete, forward-looking sentence.

=== RATIONALE ==="""
    )
    return PAY_RATIONALE_SYSTEM, user_prompt


def _fmt(val: object, prefix: str = "", suffix: str = "") -> str:
    """Format a nullable value with optional pre/suffix; returns 'N/A' for None."""
    return f"{prefix}{val}{suffix}" if val is not None else "N/A"


# ---------------------------------------------------------------------------
# Token stream — one-shot Bedrock call chunked into an async generator
# ---------------------------------------------------------------------------

async def _token_stream(settings: Settings, system: str, prompt: str) -> AsyncIterator[str]:
    """Yield chunked text tokens from a single Bedrock completion."""
    raw = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: invoke_bedrock_sync(
            settings,
            [{"role": "user", "content": system + "\n\n" + prompt}],
            max_tokens=settings.bedrock_max_tokens,
            temperature=settings.bedrock_temperature,
        ),
    )
    for i in range(0, len(raw), _CHUNK_SIZE):
        yield raw[i : i + _CHUNK_SIZE]


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
    """Yield raw rationale text tokens, then persist the completed rationale."""
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
