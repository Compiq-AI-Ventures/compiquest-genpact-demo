"""Agent runtime for Phase 2.5 — lightweight agentic reporting.

No LangChain, LangGraph, CrewAI, or AutoGen. Agents are plain Python
classes; the runtime wraps every execute() call with audit logging and
every tool.run() call with audit logging.

Usage::

    registry = ToolRegistry()
    registry.register("bedrock_invoke", BedrockInvokeTool(settings))
    runtime = AgentRuntime(pipeline_run_id, trace_id, registry)
    output = await runtime.run_agent(my_agent, input_data)
    snapshot = runtime.snapshot()   # hand to PDF builder and repo flush
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import structlog

log = structlog.get_logger()


def _summarize(obj: Any, maxlen: int = 200) -> str:
    try:
        s = repr(obj)
    except Exception:
        s = "<unrepresentable>"
    return s[:maxlen]


# ---------------------------------------------------------------------------
# In-memory audit records (written to DB by the repository layer)
# ---------------------------------------------------------------------------

@dataclass
class AgentRunLog:
    agent_name: str
    agent_run_id: uuid.UUID
    pipeline_run_id: uuid.UUID
    trace_id: uuid.UUID
    status: str          # RUNNING | COMPLETED | FAILED
    execution_order: int
    input_summary: str
    output_summary: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    pipeline_meta: dict | None = None  # structured human-readable data for PDF; not persisted to DB


@dataclass
class ToolRunLog:
    tool_name: str
    tool_run_id: uuid.UUID
    agent_run_id: uuid.UUID
    pipeline_run_id: uuid.UUID
    trace_id: uuid.UUID
    status: str          # RUNNING | SUCCESS | FAILED | TIMEOUT
    input_repr: str
    output_repr: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Any] = {}

    def register(self, name: str, tool: Any) -> None:
        self._tools[name] = tool

    def get(self, name: str) -> Any | None:
        return self._tools.get(name)


# ---------------------------------------------------------------------------
# ToolCaller — injected into agents so they route through the runtime audit
# ---------------------------------------------------------------------------

class ToolCaller:
    def __init__(self, runtime: AgentRuntime, agent_run_id: uuid.UUID) -> None:
        self._runtime = runtime
        self._agent_run_id = agent_run_id

    async def call(self, tool_name: str, **kwargs: Any) -> Any:
        return await self._runtime._call_tool(tool_name, self._agent_run_id, **kwargs)


# ---------------------------------------------------------------------------
# AgentRuntime
# ---------------------------------------------------------------------------

class AgentRuntime:
    """Sequences agents, wraps every execute/tool call with audit logging."""

    def __init__(
        self,
        pipeline_run_id: uuid.UUID,
        trace_id: uuid.UUID,
        tool_registry: ToolRegistry,
    ) -> None:
        self.pipeline_run_id = pipeline_run_id
        self.trace_id = trace_id
        self._tools = tool_registry
        self._agent_logs: list[AgentRunLog] = []
        self._tool_logs: list[ToolRunLog] = []
        self._order = 0

    async def run_agent(self, agent: Any, input_data: Any) -> Any:
        """Execute an agent, recording the run in the audit log."""
        self._order += 1
        agent_run_id = uuid.uuid4()
        entry = AgentRunLog(
            agent_name=agent.name,
            agent_run_id=agent_run_id,
            pipeline_run_id=self.pipeline_run_id,
            trace_id=self.trace_id,
            status="RUNNING",
            execution_order=self._order,
            input_summary=_summarize(input_data),
            started_at=datetime.now(timezone.utc),
        )
        self._agent_logs.append(entry)
        log.info("agent.start", agent=agent.name, order=self._order)

        tool_caller = ToolCaller(self, agent_run_id)
        t0 = time.monotonic()
        try:
            output = await agent.execute(input_data, tool_caller)
            entry.status = "COMPLETED"
            # Extract structured PDF metadata before summarising (not stored in DB)
            if isinstance(output, dict):
                entry.pipeline_meta = output.pop("_pipeline_meta", None)
            entry.output_summary = _summarize(output)
            return output
        except Exception as exc:
            entry.status = "FAILED"
            entry.error_message = str(exc)[:500]
            log.warning("agent.failed", agent=agent.name, error=str(exc))
            raise
        finally:
            entry.duration_ms = int((time.monotonic() - t0) * 1000)
            entry.completed_at = datetime.now(timezone.utc)
            log.info(
                "agent.done",
                agent=agent.name,
                status=entry.status,
                duration_ms=entry.duration_ms,
            )

    async def _call_tool(self, tool_name: str, agent_run_id: uuid.UUID, **kwargs: Any) -> Any:
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ValueError(f"Tool {tool_name!r} not registered in runtime")

        tool_run_id = uuid.uuid4()
        entry = ToolRunLog(
            tool_name=tool_name,
            tool_run_id=tool_run_id,
            agent_run_id=agent_run_id,
            pipeline_run_id=self.pipeline_run_id,
            trace_id=self.trace_id,
            status="RUNNING",
            input_repr=repr(kwargs)[:500],
            started_at=datetime.now(timezone.utc),
        )
        self._tool_logs.append(entry)
        log.info("tool.call", tool=tool_name)

        t0 = time.monotonic()
        try:
            if asyncio.iscoroutinefunction(tool.run):
                result = await tool.run(**kwargs)
            else:
                result = tool.run(**kwargs)
            entry.status = "SUCCESS"
            entry.output_repr = repr(result)[:500]
            return result
        except asyncio.CancelledError:
            entry.status = "TIMEOUT"
            raise
        except Exception as exc:
            entry.status = "FAILED"
            entry.error_message = str(exc)[:500]
            raise
        finally:
            entry.duration_ms = int((time.monotonic() - t0) * 1000)
            entry.completed_at = datetime.now(timezone.utc)

    def snapshot(self) -> dict:
        """Return all audit logs for PDF rendering and DB flush."""
        return {
            "pipeline_run_id": self.pipeline_run_id,
            "trace_id": self.trace_id,
            "agent_logs": list(self._agent_logs),
            "tool_logs": list(self._tool_logs),
        }
