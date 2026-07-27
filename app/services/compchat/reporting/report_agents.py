"""Concrete agents for the Phase 2.5 agentic reporting pipeline.

Each agent is a plain Python class with:
    name: str
    async def execute(self, input_data: dict, tools: ToolCaller) -> dict

Every agent output dict includes a ``_pipeline_meta`` key with structured,
human-readable data used exclusively by the PDF renderer (Section 9).
The runtime pops this key before storing output_summary, so it never
reaches the DB — it only lives in the AgentRunLog.pipeline_meta field
for the duration of the request.

One agent runs per report:
  NarrativeGenerationAgent  — calls BedrockInvokeTool, parses LLM JSON

The narrative is injected into the PDF as-is; there is no claim
extraction/verification/faithfulness gate on the generated text.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog

from .agent_runtime import ToolCaller
from .metric_context import MetricContextV1
from .narrative_agent import (
    PROMPT_VERSION,
    NarrativeOutput,
    NarrativeParseError,
    NarrativeTimeoutError,
    _build_system_prompt,
    _parse_output,
)

log = structlog.get_logger()

_BEDROCK_TIMEOUT_S = 15.0


# ---------------------------------------------------------------------------
# Agent 1: narrative generation
# ---------------------------------------------------------------------------

class NarrativeGenerationAgent:
    name = "NarrativeGenerationAgent"

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    async def execute(self, input_data: dict, tools: ToolCaller) -> dict:
        """
        Input keys:  ctx (MetricContextV1), attempt (int)
        Output keys: narr_out (NarrativeOutput), _pipeline_meta (dict)
        """
        ctx: MetricContextV1 = input_data["ctx"]
        attempt: int = input_data.get("attempt", 1)

        system_prompt = _build_system_prompt(ctx)
        user_content = json.dumps(ctx.to_llm_payload(), default=str, indent=2)
        messages = [{"role": "user", "content": user_content}]

        log.info("narrative_agent.start", attempt=attempt, metrics=len(ctx.metrics))
        t0 = time.monotonic()

        try:
            raw, in_tok, out_tok = await asyncio.wait_for(
                tools.call(
                    "bedrock_invoke",
                    system_prompt=system_prompt,
                    messages=messages,
                ),
                timeout=_BEDROCK_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            raise NarrativeTimeoutError(
                f"Bedrock call exceeded {_BEDROCK_TIMEOUT_S}s (attempt {attempt})"
            )

        latency_ms = int((time.monotonic() - t0) * 1000)
        sections = _parse_output(raw, ctx)
        model_id = getattr(self._settings, "bedrock_model_id", "unknown")

        narr_out = NarrativeOutput(
            sections=sections,
            model_id=model_id,
            prompt_version=PROMPT_VERSION,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=latency_ms,
            raw_output=raw,
        )

        generated = sum(1 for v in narr_out.sections.values() if v.status == "GENERATED")
        withheld = sum(1 for v in narr_out.sections.values() if v.status == "WITHHELD")
        section_names = list(narr_out.sections.keys())

        gen_names = [k for k, v in narr_out.sections.items() if v.status == "GENERATED"]
        return {
            "narr_out": narr_out,
            "_pipeline_meta": {
                # Single-line summaries for the 9B tool table
                "input_desc": (
                    f"MetricContextV1 | {len(ctx.metrics)} metrics | "
                    f"scope: {ctx.scope_label} | headcount: {ctx.headcount}"
                ),
                "output_desc": (
                    f"NarrativeOutput | {generated} GENERATED, {withheld} WITHHELD"
                ),
                # Structured fields for the 9A card (rendered as labelled sub-rows)
                "input_fields": [
                    ("Type", "MetricContextV1"),
                    ("Scope", ctx.scope_label),
                    ("Headcount", str(ctx.headcount)),
                    ("Metrics", f"{len(ctx.metrics)} pre-computed"),
                    ("Sections Available", ", ".join(ctx.sections_available) if ctx.sections_available else "all"),
                ],
                "tool_calls": [{
                    "tool": "bedrock_invoke",
                    "input": (
                        f"prompt {PROMPT_VERSION} | {len(ctx.metrics)} metrics | {in_tok:,} tokens"
                    ),
                    "output": (
                        f"{out_tok:,} tokens | {latency_ms:,}ms | {model_id}"
                    ),
                    "input_fields": [
                        ("Prompt Version", PROMPT_VERSION),
                        ("Metrics", f"{len(ctx.metrics)}"),
                        ("Input Tokens", f"{in_tok:,}"),
                    ],
                    "output_fields": [
                        ("Output Tokens", f"{out_tok:,}"),
                        ("Latency", f"{latency_ms:,}ms"),
                        ("Model", model_id),
                    ],
                }],
                "output_fields": [
                    ("Type", "NarrativeOutput"),
                    ("Sections Generated", str(generated)),
                    ("Sections Withheld", str(withheld)),
                    ("Generated", ", ".join(gen_names) if gen_names else "none"),
                ],
                "feeds_into": "PDF generation",
            },
        }
