"""ReportTracer — accumulates audit state for one report execution.

Usage pattern::

    tracer = ReportTracer(trace_id=run.id, run_id=run.id)

    async with tracer.step("fetch_population", order=4, fiscal_year=2025) as s:
        rows = await fetch(...)
        tracer.record_dataset("genpact_employee_master", rows, fiscal_year=2025)
        s.metadata["rows"] = len(rows)

    tracer.register_metric(make_record("calc:headcount", len(rows), str(len(rows))))

    snapshot = tracer.snapshot()   # → passed to pdf_builder and audit_repo.flush()

No DB access happens here. All state is in-memory until snapshot() is
called and the router flushes it.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog

from .metrics import MetricRecord

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# In-memory record types (flushed to DB by audit_repository.flush)
# ---------------------------------------------------------------------------

@dataclass
class DatasetRecord:
    source_table: str
    row_count: int
    query_filter: str
    fiscal_year: int | None
    sample_hash: str | None
    snapshot_time: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class StepRecord:
    step_name: str
    step_order: int
    status: str = "PENDING"
    started_at: float = field(default_factory=time.monotonic)
    completed_at_iso: str | None = None
    duration_ms: int | None = None
    metadata: dict = field(default_factory=dict)
    error: str | None = None


# ---------------------------------------------------------------------------
# ReportTracer
# ---------------------------------------------------------------------------

class ReportTracer:
    """Accumulates all audit state for one report execution.

    Intentionally decoupled from SQLAlchemy — it holds plain Python
    dataclasses and emits structlog events. The router flushes everything
    to DB via ``report_audit_repository.flush(tracer.snapshot())``.
    """

    TEMPLATE_VERSION = "1.0.0"
    REPORT_VERSION = "2025.1"

    def __init__(self, trace_id: uuid.UUID, run_id: uuid.UUID) -> None:
        self.trace_id = trace_id
        self.run_id = run_id
        self._steps: list[StepRecord] = []
        self._datasets: list[DatasetRecord] = []
        self._metrics: list[MetricRecord] = []
        self._log = log.bind(trace_id=str(trace_id))
        self._wall_start = time.monotonic()

    # ---- Step context manager ----

    @asynccontextmanager
    async def step(self, name: str, order: int, **meta):
        """Async context manager that records a step's timing and status.

        Any keyword args passed become the step's initial metadata.
        Callers can update ``rec.metadata`` inside the block.
        """
        rec = StepRecord(step_name=name, step_order=order, metadata=dict(meta))
        self._steps.append(rec)
        rec.status = "RUNNING"
        t0 = time.monotonic()
        self._log.info("report.step.start", step=name, order=order, **meta)
        try:
            yield rec
            rec.duration_ms = int((time.monotonic() - t0) * 1000)
            rec.completed_at_iso = datetime.now(timezone.utc).isoformat()
            rec.status = "SUCCESS"
            self._log.info(
                "report.step.done", step=name, order=order,
                duration_ms=rec.duration_ms, **rec.metadata,
            )
        except Exception as exc:
            rec.duration_ms = int((time.monotonic() - t0) * 1000)
            rec.completed_at_iso = datetime.now(timezone.utc).isoformat()
            rec.status = "FAILED"
            rec.error = str(exc)
            self._log.error(
                "report.step.failed", step=name, order=order,
                duration_ms=rec.duration_ms, error=str(exc),
            )
            raise

    # ---- Dataset provenance ----

    def record_dataset(
        self,
        source_table: str,
        rows: list,
        *,
        query_filter: str = "",
        fiscal_year: int | None = None,
    ) -> None:
        """Fingerprint a fetched dataset and store its provenance."""
        sample_ids = sorted(str(getattr(r, "id", "")) for r in rows[:100])
        sample_hash = hashlib.sha256("|".join(sample_ids).encode()).hexdigest()[:16]
        self._datasets.append(DatasetRecord(
            source_table=source_table,
            row_count=len(rows),
            query_filter=query_filter,
            fiscal_year=fiscal_year,
            sample_hash=sample_hash,
        ))
        self._log.info("report.dataset", table=source_table, rows=len(rows))

    # ---- Metric registration ----

    def register_metric(self, record: MetricRecord) -> None:
        self._metrics.append(record)
        self._log.debug(
            "report.metric",
            metric_id=record.metric_id,
            value=str(record.metric_value),
        )

    # ---- Derived helpers ----

    def source_hash(self) -> str:
        """SHA-256 of all dataset fingerprints combined — changes when any input dataset changes."""
        parts = sorted(d.sample_hash or "" for d in self._datasets)
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    def total_wall_ms(self) -> int:
        return int((time.monotonic() - self._wall_start) * 1000)

    # ---- Snapshot ----

    def snapshot(self) -> dict:
        """Return an immutable snapshot of all accumulated state.

        Passed to ``pdf_builder.build_pdf(audit_data=...)`` and
        ``report_audit_repository.flush(...)``.
        """
        return {
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "template_version": self.TEMPLATE_VERSION,
            "report_version": self.REPORT_VERSION,
            "steps": list(self._steps),
            "datasets": list(self._datasets),
            "metrics": list(self._metrics),
            "source_hash": self.source_hash(),
            "total_wall_ms": self.total_wall_ms(),
        }
