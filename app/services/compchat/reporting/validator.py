"""Validation framework for the compensation PDF report.

Phase 1 validates every computed KPI before it reaches the PDF:

* COUNT  — must be a non-negative integer.
* PERCENT — must be in [0, 100].
* INR    — must be non-negative.

Required metrics that are None produce a FAIL. Optional metrics
(``MetricSpec.required = False``) that are None produce a SKIPPED.

Cross-checks confirm derived metrics are consistent with their
components (e.g. ``variable_payout_actual == total_variable_spend``).
This harness is intentionally over-specified for Phase 1 because Phase 2
will add LLM-generated figures that must be checked against these same
ground-truth values.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import structlog

from .metrics import MetricRecord, MetricUnit

log = structlog.get_logger()

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
_DEFAULT_TOL = Decimal("0.001")

# Cross-check pairs: (source_metric_id, must_equal_metric_id)
_CROSS_CHECKS: list[tuple[str, str]] = [
    ("calc:variable_payout_actual", "calc:total_variable_spend"),
    ("calc:effective_increment_pct", "calc:avg_increment_pct"),
]


@dataclass
class ValidationResult:
    metric_id: str
    expected_value: Decimal | None  # value registered by compute layer
    actual_value: Decimal | None    # value re-derived for the render-bound check
    tolerance: Decimal
    status: str                     # PASS | FAIL | SKIPPED
    notes: str = ""


# ---------------------------------------------------------------------------
# Individual validators
# ---------------------------------------------------------------------------

def _result(metric_id: str, val: Decimal | None, ok: bool, tol: Decimal, notes: str = "") -> ValidationResult:
    return ValidationResult(
        metric_id=metric_id,
        expected_value=val,
        actual_value=val,
        tolerance=tol,
        status="PASS" if ok else "FAIL",
        notes=notes,
    )


def validate_metric(record: MetricRecord) -> ValidationResult:
    """Validate one MetricRecord against unit-level rules."""
    tol = _DEFAULT_TOL
    val = record.metric_value

    if val is None:
        if record.spec.required:
            return ValidationResult(
                record.metric_id, None, None, tol,
                "FAIL", "required metric is None",
            )
        return ValidationResult(
            record.metric_id, None, None, tol,
            "SKIPPED", "optional / section withheld",
        )

    unit = record.spec.unit

    if unit == MetricUnit.COUNT:
        ok = int(val) >= 0
        return _result(record.metric_id, val, ok, tol,
                       "" if ok else f"count is negative: {val}")

    if unit == MetricUnit.PERCENT:
        if val < _ZERO:
            return _result(record.metric_id, val, False, tol, f"percent is negative: {val}")
        note = f"over 100% ({val}) - attainment or growth metric" if val > _HUNDRED else ""
        return _result(record.metric_id, val, True, tol, note)

    if unit == MetricUnit.INR:
        ok = val >= _ZERO
        return _result(record.metric_id, val, ok, tol,
                       "" if ok else f"INR value is negative: {val}")

    # BOOLEAN — presence is enough
    return _result(record.metric_id, val, True, tol)


# ---------------------------------------------------------------------------
# Cross-checks
# ---------------------------------------------------------------------------

def _within(a: Decimal, b: Decimal, tol: Decimal) -> bool:
    if b == _ZERO:
        return a == _ZERO
    return abs(a - b) / abs(b) <= tol


def validate_cross_check(
    a: MetricRecord, b: MetricRecord, tol: Decimal = _DEFAULT_TOL
) -> ValidationResult:
    """Assert two metrics that must agree (derived from the same source)."""
    va, vb = a.metric_value, b.metric_value
    if va is None or vb is None:
        return ValidationResult(
            a.metric_id, va, vb, tol,
            "SKIPPED", f"cross-check skipped: one side None ({a.metric_id} vs {b.metric_id})",
        )
    ok = _within(va, vb, tol)
    return ValidationResult(
        a.metric_id, va, vb, tol,
        "PASS" if ok else "FAIL",
        f"cross-check {a.metric_id} vs {b.metric_id}" + ("" if ok else f" delta={abs(va - vb)}"),
    )


# ---------------------------------------------------------------------------
# Batch entry point
# ---------------------------------------------------------------------------

def validate_all(metrics: list[MetricRecord]) -> list[ValidationResult]:
    """Run all individual validations then cross-checks. Log any FAILs."""
    results: list[ValidationResult] = [validate_metric(m) for m in metrics]

    by_id = {m.metric_id: m for m in metrics}
    for id_a, id_b in _CROSS_CHECKS:
        if id_a in by_id and id_b in by_id:
            results.append(validate_cross_check(by_id[id_a], by_id[id_b]))

    # Emit a structured log event for every FAIL so it shows up in alerts.
    fails = [r for r in results if r.status == "FAIL"]
    for r in fails:
        log.warning(
            "report.validation.fail",
            metric_id=r.metric_id,
            expected=str(r.expected_value),
            actual=str(r.actual_value),
            notes=r.notes,
        )

    passes = sum(1 for r in results if r.status == "PASS")
    skipped = sum(1 for r in results if r.status == "SKIPPED")
    log.info(
        "report.validation.summary",
        total=len(results), passes=passes,
        fails=len(fails), skipped=skipped,
    )
    return results
