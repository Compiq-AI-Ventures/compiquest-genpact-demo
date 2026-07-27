"""Tool implementations for the Phase 2.5 agentic pipeline.

Each tool is a plain Python class with a ``run(**kwargs)`` method.
Tools are registered in the ToolRegistry and invoked through ToolCaller
so every call is audited by AgentRuntime.

One tool:
  BedrockInvokeTool  — async; calls AWS Bedrock via thread executor
"""

from __future__ import annotations

import asyncio
from typing import Any

from .narrative_agent import invoke_bedrock


class BedrockInvokeTool:
    """Calls AWS Bedrock (sync under the hood) from an async context.

    Returns (raw_text, input_tokens, output_tokens).
    """

    name = "bedrock_invoke"

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    async def run(
        self,
        system_prompt: str,
        messages: list[dict],
    ) -> tuple[str, int, int]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: invoke_bedrock(self._settings, system_prompt, messages),
        )
