"""Grounded narration for the CD&A executive summary (local model, optional).

The only place a language model touches the report. Its job is narrow: write
one short synthesis paragraph for the executive summary, reasoning over the
figures it is *handed* (parsed from the upload) plus the domain knowledge
base. It is forbidden from introducing any number that is not in the supplied
facts — every figure in the report proper is rendered deterministically
elsewhere.

Runs against the local Ollama endpoint using ``settings.cda_model``. If the
model is unreachable, slow, or returns nothing, the caller falls back to a
deterministic paragraph built from the parsed bullets, so report generation
never depends on the model being up.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .knowledge_base import knowledge_block
from .schema import ExecSummary

logger = logging.getLogger(__name__)

# Generous enough to cover a cold model load into memory on the first
# request (~2 min for a 9B). Ollama keeps the model resident afterwards, so
# subsequent requests return in seconds.
_TIMEOUT = 240.0

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


async def generate_exec_summary_narrative(settings: Any, es: ExecSummary) -> str:
    """Return a grounded synthesis paragraph, or a deterministic fallback.

    Never raises — any transport/parse failure degrades to the fallback.
    """
    model = getattr(settings, "cda_model", None) or getattr(settings, "ollama_model", "")
    base_url = getattr(settings, "ollama_base_url", "http://localhost:11434")
    if not model:
        return _deterministic_fallback(es)

    payload = {
        "model": model,
        "prompt": _build_prompt(es),
        "stream": False,
        # qwen3.5 is a "thinking" model: left on, its reasoning consumes the
        # token budget and the ``response`` field comes back empty. We want a
        # direct answer, so thinking is disabled. Harmless for non-thinking
        # models (Ollama ignores it).
        "think": False,
        "options": {
            "temperature": getattr(settings, "cda_temperature", 0.2),
            "num_predict": getattr(settings, "cda_max_tokens", 500),
        },
    }
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=_TIMEOUT) as client:
            resp = await client.post("/api/generate", json=payload)
            resp.raise_for_status()
            text = (resp.json().get("response") or "").strip()
    except Exception as exc:
        logger.warning("CD&A narration unavailable (model=%s): %s — using fallback", model, exc)
        return _deterministic_fallback(es)

    return text or _deterministic_fallback(es)
