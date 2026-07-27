"""Metric Registry for the compensation PDF report.

Every report KPI is declared here with a stable ``metric_id`` that never
changes across deployments, a human-readable formula, a source dataset,
and a unit. The registry is code-level — no DB table. The DB stores
computed instances via :class:`MetricRecord` (persisted in
``report_metrics``).

Rule: every ``make_record()`` call will ``KeyError`` if the metric_id is
not in REGISTRY. That is intentional — an unregistered metric cannot
silently reach the PDF or the audit log.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class MetricUnit(StrEnum):
    PERCENT = "percent"
    INR = "inr"
    COUNT = "count"
    BOOLEAN = "bool"


class MetricSection(StrEnum):
    EXEC_SUMMARY = "exec_summary"
    DATA_QUALITY = "data_quality"
    RETENTION = "retention"
    SPEND = "spend_movement"
    CORRECTIONS = "corrections"
    EQUITY = "equity"


@dataclass(frozen=True)
class MetricSpec:
    metric_id: str
    metric_name: str
    formula: str
    source_dataset: str
    unit: MetricUnit
    section: MetricSection
    required: bool = True  # False = may be None without triggering a FAIL validation


@dataclass
class MetricRecord:
    """One computed instance of a MetricSpec — what gets persisted."""

    metric_id: str
    metric_value: Decimal | None
    metric_value_str: str  # formatted exactly as it appears in the PDF
    spec: MetricSpec


# ---------------------------------------------------------------------------
# Canonical registry — extend here; never in the DB.
# ---------------------------------------------------------------------------

REGISTRY: dict[str, MetricSpec] = {
    s.metric_id: s
    for s in [
        # --- Section 1: Executive Summary ---
        MetricSpec(
            "calc:headcount", "Headcount",
            formula="COUNT(*) on population",
            source_dataset="genpact_employee_master",
            unit=MetricUnit.COUNT, section=MetricSection.EXEC_SUMMARY,
        ),
        MetricSpec(
            "calc:avg_increment_pct", "Headline Increment %",
            formula="AVG(total_increment_percent) WHERE total_increment_percent != 0",
            source_dataset="genpact_employee_master",
            unit=MetricUnit.PERCENT, section=MetricSection.EXEC_SUMMARY,
        ),
        MetricSpec(
            "calc:total_variable_spend", "Total Variable Spend",
            formula="SUM(actual_bonus_paid)",
            source_dataset="genpact_employee_master",
            unit=MetricUnit.INR, section=MetricSection.EXEC_SUMMARY,
        ),
        MetricSpec(
            "calc:correction_headcount", "Correction Exposure",
            formula="COUNT(*) WHERE pct_correction_increase > 0",
            source_dataset="genpact_employee_master",
            unit=MetricUnit.COUNT, section=MetricSection.EXEC_SUMMARY,
        ),
        # --- Section 2: Data Quality ---
        MetricSpec(
            "calc:data_quality_pct", "Data Quality Score",
            formula="AVG(field_completeness_pct) across 6 key fields",
            source_dataset="genpact_employee_master",
            unit=MetricUnit.PERCENT, section=MetricSection.DATA_QUALITY,
        ),
        # --- Section 3: Retention vs Pay (optional — may be withheld) ---
        MetricSpec(
            "calc:increased_and_quit_count", "Increased-then-Quit Count",
            formula="COUNT(*) WHERE status IN exit_statuses AND pct_base_increase > 0",
            source_dataset="genpact_employee_master",
            unit=MetricUnit.COUNT, section=MetricSection.RETENTION,
            required=False,
        ),
        MetricSpec(
            "calc:increased_and_quit_pct", "Increased-then-Quit %",
            formula="(increased_and_quit_count / headcount) * 100",
            source_dataset="genpact_employee_master",
            unit=MetricUnit.PERCENT, section=MetricSection.RETENTION,
            required=False,
        ),
        MetricSpec(
            "calc:no_lift_count", "Zero-Lift Headcount",
            formula="COUNT(*) WHERE total_increment_percent = 0",
            source_dataset="genpact_employee_master",
            unit=MetricUnit.COUNT, section=MetricSection.RETENTION,
        ),
        MetricSpec(
            "calc:no_lift_pct", "Zero-Lift %",
            formula="(no_lift_count / headcount) * 100",
            source_dataset="genpact_employee_master",
            unit=MetricUnit.PERCENT, section=MetricSection.RETENTION,
        ),
        # --- Section 4: Spend & Movement ---
        MetricSpec(
            "calc:effective_increment_pct", "Effective Increment %",
            formula="AVG(total_increment_percent) - population-weighted",
            source_dataset="genpact_employee_master",
            unit=MetricUnit.PERCENT, section=MetricSection.SPEND,
        ),
        MetricSpec(
            "calc:variable_payout_actual", "Variable Pay Actual Payout",
            formula="SUM(actual_bonus_paid)",
            source_dataset="genpact_employee_master",
            unit=MetricUnit.INR, section=MetricSection.SPEND,
        ),
        MetricSpec(
            "calc:variable_payout_target", "Variable Pay Target Payout",
            formula="SUM(base_salary * target_bonus_pct / 100)",
            source_dataset="genpact_employee_master",
            unit=MetricUnit.INR, section=MetricSection.SPEND,
        ),
        MetricSpec(
            "calc:variable_payout_attainment_pct", "Variable Pay Attainment %",
            formula="(variable_payout_actual / variable_payout_target) * 100",
            source_dataset="genpact_employee_master",
            unit=MetricUnit.PERCENT, section=MetricSection.SPEND,
            required=False,
        ),
        MetricSpec(
            "calc:promotion_count", "Promotions",
            formula="COUNT(*) WHERE promotion_flag = TRUE",
            source_dataset="genpact_employee_master",
            unit=MetricUnit.COUNT, section=MetricSection.SPEND,
        ),
        MetricSpec(
            "calc:promotion_pct", "Promotion Rate",
            formula="(promotion_count / headcount) * 100",
            source_dataset="genpact_employee_master",
            unit=MetricUnit.PERCENT, section=MetricSection.SPEND,
        ),
        # --- Section 6: Equity ---
        MetricSpec(
            "calc:total_lti_participants", "LTI Participants",
            formula="COUNT(*) WHERE lti_eligible = TRUE",
            source_dataset="genpact_employee_master",
            unit=MetricUnit.COUNT, section=MetricSection.EQUITY,
        ),
    ]
}


def make_record(metric_id: str, value: float | int | None, value_str: str) -> MetricRecord:
    """Create a MetricRecord from a registered metric_id.

    Raises ``KeyError`` for any unregistered id — intentional guard so no
    unregistered KPI can silently reach the PDF or audit log.
    """
    spec = REGISTRY[metric_id]
    return MetricRecord(
        metric_id=metric_id,
        metric_value=Decimal(str(value)) if value is not None else None,
        metric_value_str=value_str,
        spec=spec,
    )
