"""MetricContextV1 — the only interface between deterministic Python and the LLM.

Built from a ReportTracer snapshot + ReportData. Stripped of tenant/trace
identifiers before being sent to Bedrock. Stored verbatim in
``narrative_generations.context_payload`` for replay and audit.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from .metrics import MetricRecord
from .queries import ReportData


class MetricUnit(StrEnum):
    PERCENT = "percent"
    INR = "inr"
    COUNT = "count"
    BOOL = "bool"


class MetricEntry(BaseModel):
    metric_id: str
    name: str
    value: Decimal | None
    unit: str
    value_str: str
    section: str
    required: bool = True


class MetricContextV1(BaseModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    contract_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    report_id: uuid.UUID
    trace_id: uuid.UUID
    tenant_id: uuid.UUID
    fiscal_year: int
    scope_label: str
    headcount: int
    generated_at: str
    sections_available: list[str]
    sections_withheld: list[str]
    metrics: list[MetricEntry]
    prompt_version: str = "narrative-v1.0.0"

    def to_llm_payload(self) -> dict:
        """Strip tenant/trace IDs before sending to LLM (privacy boundary)."""
        d = self.model_dump(mode="json")
        for k in ("tenant_id", "trace_id", "contract_id"):
            d.pop(k, None)
        return d

    def metric_lookup(self) -> dict[str, MetricEntry]:
        return {m.metric_id: m for m in self.metrics}

    def value_whitelist_text(self) -> str:
        """Formatted whitelist injected into the system prompt at runtime."""
        lines = [f"  {m.metric_id} -> {m.value_str!r}" for m in self.metrics]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

_ALL_SECTIONS = [
    "exec_summary",
    "spend_analysis",
    "promotion_commentary",
    "correction_commentary",
    "equity_commentary",
    "data_quality_commentary",
]

_SECTION_METRIC_MAP: dict[str, list[str]] = {
    "exec_summary": [
        "calc:headcount", "calc:avg_increment_pct",
        "calc:total_variable_spend", "calc:correction_headcount",
        "calc:data_quality_pct",
    ],
    "spend_analysis": [
        "calc:effective_increment_pct", "calc:variable_payout_actual",
        "calc:variable_payout_target", "calc:variable_payout_attainment_pct",
    ],
    "promotion_commentary": [
        "calc:promotion_count", "calc:promotion_pct", "calc:headcount",
    ],
    "correction_commentary": [
        "calc:correction_headcount", "calc:headcount",
    ],
    "equity_commentary": [
        "calc:total_lti_participants", "calc:headcount",
    ],
    "data_quality_commentary": [
        "calc:data_quality_pct",
    ],
}


def build_context(
    report_id: uuid.UUID,
    trace_id: uuid.UUID,
    tenant_id: uuid.UUID,
    data: ReportData,
    metric_records: list[MetricRecord],
) -> MetricContextV1:
    """Assemble a MetricContextV1 from computed report data and metric records."""
    by_id = {m.metric_id: m for m in metric_records}

    entries: list[MetricEntry] = []
    for mid, rec in by_id.items():
        entries.append(MetricEntry(
            metric_id=mid,
            name=rec.spec.metric_name,
            value=rec.metric_value,
            unit=str(rec.spec.unit),
            value_str=rec.metric_value_str,
            section=str(rec.spec.section),
            required=rec.spec.required,
        ))

    withheld_section_names = [name for name, _ in data.withheld_sections]

    # Map withheld section names to context section keys
    withheld_keys: list[str] = []
    if not data.retention_available:
        withheld_keys.append("promotion_commentary")
    if "Corrections by Job Family" in withheld_section_names:
        withheld_keys.append("correction_commentary")
    if "Retention vs Pay" in withheld_section_names:
        withheld_keys.append("spend_analysis")

    available_keys = [s for s in _ALL_SECTIONS if s not in withheld_keys]

    return MetricContextV1(
        report_id=report_id,
        trace_id=trace_id,
        tenant_id=tenant_id,
        fiscal_year=data.fiscal_year,
        scope_label=data.scope_label,
        headcount=data.headcount,
        generated_at=data.generated_at,
        sections_available=available_keys,
        sections_withheld=withheld_keys,
        metrics=entries,
    )
