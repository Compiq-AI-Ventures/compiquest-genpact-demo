"""Unit tests for the CompChat batch report (metrics + assembly + PDF).

Pure logic only — no DB, no LLM. Rows are lightweight namespaces with
the same attributes as ``TessotBaseData``.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.compchat.reporting import builder, metrics
from app.services.compchat.reporting import pdf as report_pdf


def _row(**kw) -> SimpleNamespace:
    base = {
        "employee_id": "E1",
        "employee_name": "A",
        "manager_employee_id": "M1",
        "status": "ACTIVE",
        "exit_classification": "",
        "total_increment_percent": 8.0,
        "base_salary": 1_000_000,
        "actual_bonus_paid": 100_000,
        "target_bonus_pct": 10.0,
        "performance_rating": 3.0,
        "promotion_flag": False,
        "job_family": "Engineering",
        "benchmark_p25": 900_000,
        "benchmark_p50": 1_100_000,
        "lti_eligible": True,
        "lti_type": "RSU",
    }
    base.update(kw)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def test_increased_but_quit_counts_voluntary_exits():
    rows = [
        _row(total_increment_percent=8),  # active, increased
        _row(total_increment_percent=6, status="INACTIVE", exit_classification="Voluntary"),
        _row(total_increment_percent=5, status="INACTIVE", exit_classification="Involuntary"),
        _row(total_increment_percent=0),  # no increase
    ]
    increased, quit_after, pct = metrics.increased_but_quit(rows)
    assert increased == 3  # three had >0 increment
    assert quit_after == 1  # only the voluntary exit
    assert abs(pct - 33.3) < 0.05


def test_no_lift_band_uses_ceiling():
    rows = [_row(total_increment_percent=2), _row(total_increment_percent=3), _row(total_increment_percent=9)]
    assert metrics.no_lift_band(rows) == 2  # 2% and 3% are <= 3% ceiling


def test_corrections_rank_by_cost_and_skip_missing_benchmark():
    rows = [
        _row(job_family="Eng", base_salary=900_000, benchmark_p25=950_000, benchmark_p50=1_100_000),
        _row(job_family="Sales", base_salary=1_050_000, benchmark_p25=900_000, benchmark_p50=1_000_000),
        _row(job_family="Ops", base_salary=500_000, benchmark_p50=0),  # no benchmark -> skipped
    ]
    families, total = metrics.corrections_by_family(rows)
    fam_names = [f["job_family"] for f in families]
    assert "Ops" not in fam_names  # benchmark missing
    assert families[0]["job_family"] == "Eng"  # highest cost to close first
    assert families[0]["under_band_count"] == 1  # base < P25
    assert total == 200_000  # only Eng is under P50 (1.1M - 0.9M)


def test_equity_participation_groups_by_type():
    rows = [_row(lti_type="RSU"), _row(lti_type="RSU"), _row(lti_type="Options"), _row(lti_eligible=False, lti_type="")]
    plans, total = metrics.equity_participation(rows)
    assert total == 3
    assert {p["plan_type"]: p["participant_count"] for p in plans} == {"RSU": 2, "Options": 1}


def test_variable_payout_vs_target():
    rows = [_row(base_salary=1_000_000, target_bonus_pct=10, actual_bonus_paid=120_000)]
    actual, target, pct = metrics.variable_payout_vs_target(rows)
    assert actual == 120_000
    assert target == 100_000  # 10% of 1M
    assert abs(pct - 120.0) < 0.05


# ---------------------------------------------------------------------------
# Assembly + data-quality withholding
# ---------------------------------------------------------------------------
def test_build_report_withholds_corrections_when_benchmarks_missing():
    rows = [_row(benchmark_p25=0, benchmark_p50=0) for _ in range(4)]
    report = builder.build_report(rows, scope_label="Whole tenant", fiscal_year=2026, trace_id="t1")
    assert "Corrections by Job Family" in report.data_quality.withheld_sections
    assert report.corrections is None
    assert report.executive_summary.headcount == 4
    # The audit trail records every metric that ran.
    assert any(e.function == "metrics.increased_but_quit" for e in report.audit_trail)


def test_build_report_full_path_populates_sections():
    rows = [_row(), _row(promotion_flag=True), _row(total_increment_percent=2)]
    report = builder.build_report(rows, scope_label="Whole tenant", fiscal_year=2026, trace_id="t2")
    assert report.corrections is not None
    assert report.spend_movement.promotion_count == 1
    assert report.equity.total_participants == 3
    assert report.data_quality.verdict in {"GOOD", "PARTIAL", "POOR"}


# ---------------------------------------------------------------------------
# Provenance, validation/reconciliation, reproducibility metadata
# ---------------------------------------------------------------------------
def test_metric_audit_entries_carry_formula_provenance_and_latency():
    rows = [_row(), _row(promotion_flag=True)]
    report = builder.build_report(rows, scope_label="Whole tenant", fiscal_year=2026, trace_id="t4")
    eff = next(e for e in report.audit_trail if e.function == "metrics.effective_increment_pct")
    assert eff.formula  # the exact arithmetic is recorded
    assert eff.source_tables == ["tessot_base_data"]
    assert eff.duration_ms is not None and eff.duration_ms >= 0
    # Pure gate/persistence steps carry no formula.
    gate = next(e for e in report.audit_trail if e.function == "builder.resolve_scope")
    assert gate.formula is None and gate.duration_ms is None


def test_validation_reconciles_every_headline_to_its_tool_output():
    rows = [_row(), _row(promotion_flag=True), _row(total_increment_percent=2)]
    report = builder.build_report(rows, scope_label="Whole tenant", fiscal_year=2026, trace_id="t5")
    v = report.validation
    assert v.checks_executed > 0
    assert v.failed == 0
    assert v.passed == v.checks_executed
    assert v.faithfulness_score >= 100.0  # all claims verified
    assert v.release_status == "APPROVED"
    assert all(c.status == "PASS" and c.reported == c.tool_output for c in v.checks)
    # Each check carries a citation (tool_result_id) and an EXACT_MATCH verdict.
    assert all(c.tool_result_id.startswith("metrics.") for c in v.checks)
    assert all(c.comparison == "EXACT_MATCH" for c in v.checks)
    eff = next(c for c in v.checks if c.metric == "Headline increment %")
    assert eff.tool_result_id == "metrics.effective_increment_pct"
    # The corrections check appears only when the section is not withheld.
    assert any(c.metric == "Total cost to close" for c in v.checks)


def test_validation_skips_corrections_check_when_withheld():
    rows = [_row(benchmark_p25=0, benchmark_p50=0) for _ in range(4)]
    report = builder.build_report(rows, scope_label="Whole tenant", fiscal_year=2026, trace_id="t6")
    assert report.corrections is None
    assert not any(c.metric == "Total cost to close" for c in report.validation.checks)
    assert report.validation.failed == 0


def test_metadata_declares_rule_based_engine_and_provenance():
    rows = [_row()]
    trace_id = "abc123def4567890abc123def4567890"  # 32-hex, like uuid4().hex
    report = builder.build_report(
        rows, scope_label="Whole tenant", fiscal_year=2026, trace_id=trace_id,
        request_context={"code_version": "deadbeef"},
    )
    m = report.metadata
    assert "Rule-based" in m.generation_engine
    assert m.trace_id == trace_id
    assert m.report_id == "REP_abc123def456"  # trace_id[:12], prefixed
    assert m.dataset.source_tables == ["tessot_base_data"]
    assert m.dataset.row_count == 1
    assert m.dataset.snapshot_time == report.generated_at
    assert m.code_version == "deadbeef"


# ---------------------------------------------------------------------------
# Chat-side claim verification (the verification demo, run through the real
# validator) — citations on verified figures, faithfulness, release decision
# ---------------------------------------------------------------------------
def test_verification_demo_cites_verified_figures_and_blocks_fabrication():
    from app.services.compchat.reporting.verification_demo import build_verification_demo

    demo = build_verification_demo()
    good, bad = demo.examples[0], demo.examples[1]
    # The grounded example releases with full faithfulness and every figure cited.
    assert good.passed and good.release_status == "APPROVED"
    assert good.figures_verified == good.figures_checked
    assert good.faithfulness_score >= 100.0
    assert all(c.citation for c in good.checks if c.status == "VERIFIED")
    # The fabricated example is blocked, with the made-up figure left uncited.
    assert not bad.passed and bad.release_status == "BLOCKED"
    assert bad.faithfulness_score < 100.0
    assert any(c.status == "BLOCKED" and c.citation is None for c in bad.checks)


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------
def test_pdf_renders_valid_bytes():
    rows = [_row(), _row(status="INACTIVE", exit_classification="Voluntary")]
    report = builder.build_report(rows, scope_label="Whole tenant", fiscal_year=2026, trace_id="t3")
    data = report_pdf.render(report)
    assert isinstance(data, bytes)
    assert data[:4] == b"%PDF"
    assert len(data) > 1000
