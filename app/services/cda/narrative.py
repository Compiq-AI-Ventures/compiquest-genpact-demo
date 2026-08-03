"""Grounded narration for the CD&A executive summary (AWS Bedrock, optional).

The only place a language model touches the report. Its job is narrow: write
one short synthesis paragraph for the executive summary, reasoning over the
figures it is *handed* (parsed from the upload) plus the domain knowledge
base. It is forbidden from introducing any number that is not in the supplied
facts — every figure in the report proper is rendered deterministically
elsewhere.

Runs against AWS Bedrock using ``settings.bedrock_model_id``. If the
model is unreachable, slow, or returns nothing, the caller falls back to a
deterministic paragraph built from the parsed bullets, so report generation
never depends on the model being up.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.services.bedrock_client import get_bedrock_client

from .knowledge_base import knowledge_block
from .schema import ExecSummary

logger = logging.getLogger(__name__)

_APP_JSON = "application/json"

# Bedrock has no cold-start model load, so a short timeout is enough.
_TIMEOUT = 15.0

_SYSTEM = (
    "You are a compensation-committee analyst drafting the opening paragraph of "
    "a Compensation Discussion & Analysis (CD&A) report. Write in formal, plain "
    "business English."
)


def _facts_block(es: ExecSummary) -> str:
    lines: list[str] = []
    if es.bullets:
        lines.append("SUMMARY POINTS:")
        lines += [f"- {b}" for b in es.bullets]
    if es.cards:
        lines.append("\nKEY METRICS:")
        for c in es.cards:
            piece = f"- {c.metric}: {c.value}"
            extra = " | ".join(x for x in (c.detail, c.comparison, c.period) if x)
            if extra:
                piece += f" ({extra})"
            lines.append(piece)
    return "\n".join(lines)


def _build_prompt(es: ExecSummary) -> str:
    return (
        _SYSTEM
        + "\n\n"
        + knowledge_block()
        + "\n\n=== FACTS FOR THIS REPORT (the only figures you may cite) ===\n"
        + _facts_block(es)
        + "\n\n=== TASK ===\n"
        "Write a single cohesive paragraph (3-5 sentences, ~90-140 words) that "
        "synthesises the executive-summary facts above into a narrative opening: "
        "what the compensation picture looks like this cycle, what changed, and "
        "the one or two priorities it implies.\n\n"
        "=== HARD RULES ===\n"
        "- Use ONLY the figures listed under FACTS. Never invent, round, or alter a "
        "number, percentage, or dollar amount. If a figure is not listed, do not state it.\n"
        "- Do not use the acronyms 'JVRE' or 'compa-ratio' in the output; translate "
        "them into plain terms (e.g. 'the recommendation engine', 'pay relative to market').\n"
        "- Start immediately with the first sentence. No heading, label, or preamble.\n"
        "- Output the paragraph only."
    )


def _deterministic_fallback(es: ExecSummary) -> str:
    """A safe, number-faithful paragraph built without a model."""
    if es.bullets:
        # The parsed bullets are already committee-grade prose; stitch the
        # first few into a compact opening.
        return " ".join(es.bullets[:3])
    if es.cards:
        head = es.cards[0]
        return (
            f"This cycle's compensation summary centres on {head.metric.lower()} "
            f"of {head.value}. The key metrics that follow detail the drivers "
            f"behind the year's compensation outcomes and the priorities they imply."
        )
    return ""


def _invoke_bedrock(settings: Any, prompt: str, max_tokens: int, temperature: float) -> str:
    client = get_bedrock_client(settings)
    body = json.dumps({
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    })
    resp = client.invoke_model(
        modelId=settings.bedrock_model_id,
        body=body,
        contentType=_APP_JSON,
        accept=_APP_JSON,
    )
    result = json.loads(resp["body"].read())
    return (result.get("choices") or [{}])[0].get("message", {}).get("content", "")


async def generate_exec_summary_narrative(settings: Any, es: ExecSummary) -> str:
    """Return a grounded synthesis paragraph, or a deterministic fallback.

    Never raises — any transport/parse failure degrades to the fallback.
    """
    model = getattr(settings, "bedrock_model_id", "")
    if not model:
        return _deterministic_fallback(es)

    try:
        text = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(
                None,
                lambda: _invoke_bedrock(
                    settings,
                    _build_prompt(es),
                    max_tokens=getattr(settings, "cda_max_tokens", 500),
                    temperature=getattr(settings, "cda_temperature", 0.2),
                ),
            ),
            timeout=_TIMEOUT,
        )
        text = text.strip()
    except Exception as exc:
        logger.warning("CD&A narration unavailable (model=%s): %s — using fallback", model, exc)
        return _deterministic_fallback(es)

    return text or _deterministic_fallback(es)
