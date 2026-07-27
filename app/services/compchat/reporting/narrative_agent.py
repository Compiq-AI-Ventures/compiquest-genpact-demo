"""Narrative Agent — single LLM call to Bedrock/DeepSeek.

The agent receives a MetricContextV1 payload, calls Bedrock once, and
returns a NarrativeOutput. It has no tools, no DB access, and cannot
generate new metrics.

Guardrails applied here:
  - Value whitelist injected into the system prompt at runtime.
  - Temperature = 0.1 for near-deterministic phrasing.
  - Timeout = 15s; raises NarrativeTimeoutError on expiry.
  - Raw LLM output stored verbatim regardless of parse success.
  - JSON parse failure raises NarrativeParseError (caller blocks narrative).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.services.bedrock_client import get_bedrock_client

from .metric_context import MetricContextV1

log = structlog.get_logger()

PROMPT_VERSION = "narrative-v1.0.0"
_TIMEOUT_S = 15.0
_TEMPERATURE = 0.1
_MAX_TOKENS = 1200

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_BODY = """\
You are a compensation analytics narrator for an enterprise HR platform.
Your sole function is to write plain-English interpretations of
pre-computed compensation metrics for a management audience.

STRICT CONSTRAINTS - violating any constraint produces an invalid response:

1. NUMERIC AUTHORITY: Every number, percentage, currency amount, or count
   you write MUST be taken verbatim from the input metrics payload.
   Do NOT round, adjust, derive, infer, or estimate any value.

2. NO DATABASE ACCESS: You have no tools. You cannot query data.
   If a metric is missing from the payload, state it is unavailable.

3. NO COMPARISONS: Do not compare values to prior periods, industry
   benchmarks, or peer companies unless those values are in the payload.

4. PRIVACY: Do not mention individual employee names, IDs, or roles.

5. CITATIONS: Every sentence containing a metric must include the
   metric_id in parentheses. Example: "The promotion rate was 8.2%
   (calc:promotion_pct)."

6. HEDGED LANGUAGE: Use "suggests", "indicates", "shows", "was reported".
   Never use "proves", "caused", "will", or forward-looking statements.

7. OUTPUT FORMAT: Return ONLY valid JSON matching the schema below.
   No markdown, no preamble, no trailing text outside the JSON.

8. TOKEN BUDGET: Each section narrative must be under 120 words.
   Executive summary must be under 80 words.

9. WITHHELD SECTIONS: If a section appears in sections_withheld,
   set its status to "WITHHELD" and text to null.

OUTPUT SCHEMA (return exactly this structure):
{
  "sections": {
    "exec_summary":           {"status": "GENERATED"|"WITHHELD", "text": "...|null", "metric_ids_cited": []},
    "spend_analysis":         {"status": "GENERATED"|"WITHHELD", "text": "...|null", "metric_ids_cited": []},
    "promotion_commentary":   {"status": "GENERATED"|"WITHHELD", "text": "...|null", "metric_ids_cited": []},
    "correction_commentary":  {"status": "GENERATED"|"WITHHELD", "text": "...|null", "metric_ids_cited": []},
    "equity_commentary":      {"status": "GENERATED"|"WITHHELD", "text": "...|null", "metric_ids_cited": []},
    "data_quality_commentary":{"status": "GENERATED"|"WITHHELD", "text": "...|null", "metric_ids_cited": []}
  }
}

If you cannot produce a compliant section, set status "WITHHELD".\
"""

_EXPECTED_SECTIONS = {
    "exec_summary",
    "spend_analysis",
    "promotion_commentary",
    "correction_commentary",
    "equity_commentary",
    "data_quality_commentary",
}


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------

@dataclass
class NarrativeSection:
    status: str          # GENERATED | WITHHELD
    text: str | None
    metric_ids_cited: list[str] = field(default_factory=list)
    withheld_reason: str | None = None
    word_count: int = 0


@dataclass
class NarrativeOutput:
    sections: dict[str, NarrativeSection]
    model_id: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    raw_output: str

    def generated_texts(self) -> dict[str, str | None]:
        """Return {section: text} for GENERATED sections only."""
        return {
            k: v.text
            for k, v in self.sections.items()
            if v.status == "GENERATED" and v.text
        }


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class NarrativeTimeoutError(RuntimeError):
    pass


class NarrativeParseError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class NarrativeAgent:
    def __init__(self, settings: Any) -> None:
        self._settings = settings

    async def generate(
        self,
        ctx: MetricContextV1,
        attempt: int = 1,
    ) -> NarrativeOutput:
        system_prompt = _build_system_prompt(ctx)
        user_content = json.dumps(ctx.to_llm_payload(), default=str, indent=2)
        messages = [{"role": "user", "content": user_content}]

        log.info(
            "report.narrative.start",
            trace_id=str(ctx.trace_id),
            prompt_version=PROMPT_VERSION,
            attempt=attempt,
            metrics_count=len(ctx.metrics),
        )
        t0 = time.monotonic()

        try:
            loop = asyncio.get_running_loop()
            raw, in_tok, out_tok = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: invoke_bedrock(self._settings, system_prompt, messages),
                ),
                timeout=_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            log.warning(
                "report.narrative.timeout",
                trace_id=str(ctx.trace_id),
                attempt=attempt,
                timeout_s=_TIMEOUT_S,
            )
            raise NarrativeTimeoutError(f"Bedrock call exceeded {_TIMEOUT_S}s")

        latency_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "report.narrative.done",
            trace_id=str(ctx.trace_id),
            attempt=attempt,
            latency_ms=latency_ms,
            input_tokens=in_tok,
            output_tokens=out_tok,
        )

        sections = _parse_output(raw, ctx)

        return NarrativeOutput(
            sections=sections,
            model_id=getattr(self._settings, "bedrock_model_id", "unknown"),
            prompt_version=PROMPT_VERSION,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=latency_ms,
            raw_output=raw,
        )


# ---------------------------------------------------------------------------
# Bedrock invocation
# ---------------------------------------------------------------------------

def invoke_bedrock(
    settings: Any,
    system_prompt: str,
    messages: list[dict],
) -> tuple[str, int, int]:
    """Synchronous Bedrock call. Run in executor — never call directly in async context."""
    client = get_bedrock_client(settings)

    # DeepSeek on Bedrock uses the messages API; system prompt is a top-level field
    # on some model families. For Anthropic-hosted models it goes in system param.
    # Adjust body shape based on model ID prefix.
    model_id = getattr(settings, "bedrock_model_id", "")

    if "anthropic" in model_id or "claude" in model_id.lower():
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "system": system_prompt,
            "messages": messages,
            "max_tokens": _MAX_TOKENS,
            "temperature": _TEMPERATURE,
        })
    else:
        # DeepSeek / generic messages API — prepend system as first message
        full_messages = [{"role": "user", "content": system_prompt + "\n\n---\n\n" + messages[0]["content"]}]
        body = json.dumps({
            "messages": full_messages,
            "max_tokens": _MAX_TOKENS,
            "temperature": _TEMPERATURE,
        })

    resp = client.invoke_model(
        modelId=model_id,
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    result = json.loads(resp["body"].read())

    # Normalise response shape across model families
    if "content" in result:
        # Anthropic shape
        text = result["content"][0].get("text", "")
        in_tok = result.get("usage", {}).get("input_tokens", 0)
        out_tok = result.get("usage", {}).get("output_tokens", 0)
    elif "choices" in result:
        # OpenAI-compatible shape (DeepSeek)
        text = result["choices"][0].get("message", {}).get("content", "")
        usage = result.get("usage", {})
        in_tok = usage.get("prompt_tokens", 0)
        out_tok = usage.get("completion_tokens", 0)
    else:
        text = str(result)
        in_tok = out_tok = 0

    return text, in_tok, out_tok


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_system_prompt(ctx: MetricContextV1) -> str:
    whitelist = ctx.value_whitelist_text()
    return (
        _SYSTEM_PROMPT_BODY
        + f"\n\nAUTHORISED VALUES (copy these strings verbatim for each metric):\n{whitelist}"
    )


# ---------------------------------------------------------------------------
# Output parser
# ---------------------------------------------------------------------------

def _parse_output(raw: str, ctx: MetricContextV1) -> dict[str, NarrativeSection]:
    """Parse and validate the LLM JSON response.

    Any parse failure raises NarrativeParseError. Missing sections are
    filled as WITHHELD so the pipeline always has a complete dict.
    """
    clean = raw.strip()
    # Strip markdown code fences if the model wrapped its output
    if clean.startswith("```"):
        clean = "\n".join(
            line for line in clean.splitlines()
            if not line.startswith("```")
        ).strip()

    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError as exc:
        raise NarrativeParseError(f"LLM response is not valid JSON: {exc}") from exc

    raw_sections = parsed.get("sections")
    if not isinstance(raw_sections, dict):
        raise NarrativeParseError("LLM response missing 'sections' dict")

    # Determine which sections should be withheld per context
    withheld = set(ctx.sections_withheld)

    result: dict[str, NarrativeSection] = {}
    for key in _EXPECTED_SECTIONS:
        sec_data = raw_sections.get(key, {})
        if not isinstance(sec_data, dict):
            sec_data = {}

        if key in withheld:
            result[key] = NarrativeSection(
                status="WITHHELD",
                text=None,
                withheld_reason="section marked withheld in MetricContext",
            )
            continue

        status = sec_data.get("status", "WITHHELD")
        text = sec_data.get("text") or None
        cited = sec_data.get("metric_ids_cited") or []

        if status == "GENERATED" and not text:
            status = "WITHHELD"

        word_count = len(text.split()) if text else 0

        result[key] = NarrativeSection(
            status=status,
            text=text,
            metric_ids_cited=cited if isinstance(cited, list) else [],
            withheld_reason=sec_data.get("withheld_reason"),
            word_count=word_count,
        )

    return result
