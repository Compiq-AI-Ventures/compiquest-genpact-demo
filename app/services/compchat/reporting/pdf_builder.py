"""PDF generation for the compensation report using fpdf2 (>=2.8.7).

Consumes a :class:`~queries.ReportData` dataclass and returns raw PDF
bytes. No DB access happens here - all data was resolved by queries.py.
"""

from __future__ import annotations

from fpdf import FPDF

from .queries import ReportData

# Palette
_ACCENT = (0, 102, 204)
_DARK = (50, 50, 50)
_GREY = (230, 230, 230)
_WHITE = (255, 255, 255)
_MUTED = (100, 100, 100)
_RED = (180, 0, 0)

# A4 portrait: effective width = 210 - 15 - 15 = 180 mm
_EPW = 180


def _safe(text: str, maxlen: int = 9999) -> str:
    """Strip non-Latin-1 characters so fpdf2 core fonts never crash."""
    return text.encode("latin-1", errors="replace").decode("latin-1")[:maxlen]


def _fmt_inr(v: int | float | None) -> str:
    if v is None:
        return "N/A"
    return f"INR {int(v):,}"


def _fmt_pct(v: float | None, dec: int = 1) -> str:
    if v is None:
        return "N/A"
    return f"{round(v, dec)}%"


# ---------------------------------------------------------------------------
# PDF subclass with header / footer
# ---------------------------------------------------------------------------

class _PDF(FPDF):
    _fy: int = 0

    def header(self) -> None:
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*_DARK)
        self.cell(
            0, 7, f"Compensation Report - FY{self._fy}",
            align="R", new_x="LMARGIN", new_y="NEXT",
        )
        self.set_draw_color(200, 200, 200)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(2)

    def footer(self) -> None:
        self.set_y(-14)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------

def _sec(pdf: _PDF, num: str, title: str) -> None:
    pdf.set_fill_color(*_ACCENT)
    pdf.set_text_color(*_WHITE)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, f"  {num}. {title}", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_DARK)
    pdf.ln(2)


def _kv(pdf: _PDF, label: str, value: str, lw: int = 90) -> None:
    """Key-value row spanning the full effective width."""
    vw = _EPW - lw
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(lw, 6, label, border=1)
    pdf.set_font("Helvetica", size=9)
    pdf.cell(vw, 6, value, border=1, new_x="LMARGIN", new_y="NEXT")


def _th(pdf: _PDF, cols: list[tuple[str, float]]) -> None:
    """Table header row."""
    pdf.set_fill_color(*_GREY)
    pdf.set_font("Helvetica", "B", 9)
    for label, w in cols:
        pdf.cell(w, 7, label, border=1, fill=True, align="C")
    pdf.ln()


def _tr(pdf: _PDF, cells: list[tuple[str, float]], aligns: list[str] | None = None) -> None:
    """Table data row. First cell left-aligned, rest right-aligned by default."""
    pdf.set_font("Helvetica", size=9)
    for i, (txt, w) in enumerate(cells):
        align = (aligns[i] if aligns else None) or ("L" if i == 0 else "R")
        pdf.cell(w, 6, str(txt), border=1, align=align)
    pdf.ln()


def _note(pdf: _PDF, text: str, color: tuple = _MUTED) -> None:
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(*color)
    pdf.multi_cell(0, 4, _safe(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_DARK)
    pdf.ln(1)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _subsec(pdf: _PDF, letter: str, title: str) -> None:
    """Sub-section header inside the appendix (lighter weight than _sec)."""
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*_ACCENT)
    pdf.cell(0, 6, f"  {letter}. {title}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_DARK)
    pdf.ln(1)


def _render_audit_appendix(pdf: _PDF, audit_data: dict) -> None:
    """Five-sub-section structured appendix (7A-7E)."""
    pdf.add_page()
    _sec(pdf, "7", "Appendix - Audit Foundation")

    # 7A — Report Metadata
    _subsec(pdf, "7A", "Report Metadata")
    for label, val in [
        ("Trace ID", str(audit_data.get("trace_id", "N/A"))),
        ("Run ID", str(audit_data.get("run_id", "N/A"))),
        ("Template Version", audit_data.get("template_version", "N/A")),
        ("Report Version", audit_data.get("report_version", "N/A")),
        ("Source Hash", audit_data.get("source_hash", "N/A")),
        ("Wall Time (ms)", str(audit_data.get("total_wall_ms", "N/A"))),
    ]:
        _kv(pdf, _safe(label), _safe(str(val), 72))
    pdf.ln(4)

    # 7B — Execution Summary (steps)
    _subsec(pdf, "7B", "Execution Summary")
    steps = audit_data.get("steps", [])
    if steps:
        _th(pdf, [("#", 8), ("Step Name", 60), ("Status", 30), ("ms", 22), ("Completed", 60)])
        for s in steps:
            _tr(pdf, [
                (_safe(str(s.step_order)), 8),
                (_safe(str(s.step_name), 38), 60),
                (_safe(str(s.status)), 30),
                (_safe(str(s.duration_ms or "-")), 22),
                (_safe(str(s.completed_at_iso or "-"), 24), 60),
            ], aligns=["C", "L", "C", "R", "L"])
    else:
        _note(pdf, "No step records.")
    pdf.ln(4)

    # 7C — Dataset Provenance
    _subsec(pdf, "7C", "Dataset Provenance")
    datasets = audit_data.get("datasets", [])
    if datasets:
        _th(pdf, [("Source Table", 50), ("FY", 18), ("Rows", 16), ("Query Filter", 64), ("Sample Hash", 32)])
        for d in datasets:
            _tr(pdf, [
                (_safe(str(d.source_table), 28), 50),
                (_safe(str(d.fiscal_year or "-")), 18),
                (_safe(str(d.row_count)), 16),
                (_safe(str(d.query_filter or "-"), 40), 64),
                (_safe(str(d.sample_hash or "-"), 18), 32),
            ], aligns=["L", "C", "R", "L", "L"])
    else:
        _note(pdf, "No dataset records.")
    pdf.ln(4)

    # 7D — Metric Registry Summary
    _subsec(pdf, "7D", "Metric Registry Summary")
    metrics = audit_data.get("metrics", [])
    if metrics:
        _th(pdf, [("Metric ID", 62), ("Name", 60), ("Unit", 18), ("Value", 24), ("Sec", 16)])
        for m in metrics:
            _tr(pdf, [
                (_safe(str(m.metric_id), 36), 62),
                (_safe(str(m.spec.metric_name), 34), 60),
                (_safe(str(m.spec.unit)), 18),
                (_safe(str(m.metric_value_str), 14), 24),
                (_safe(str(m.spec.section), 10), 16),
            ], aligns=["L", "L", "C", "R", "L"])
    else:
        _note(pdf, "No metrics registered.")
    pdf.ln(4)

    # 7E — Validation Summary
    _subsec(pdf, "7E", "Validation Summary")
    validations = audit_data.get("validations", [])
    if validations:
        _th(pdf, [("Metric ID", 62), ("Status", 20), ("Expected", 50), ("Notes", 48)])
        for v in validations:
            status_color = _RED if v["status"] == "FAIL" else _DARK
            pdf.set_font("Helvetica", size=9)
            pdf.cell(62, 6, _safe(str(v["metric_id"]), 36), border=1)
            pdf.set_text_color(*status_color)
            pdf.cell(20, 6, _safe(v["status"]), border=1, align="C")
            pdf.set_text_color(*_DARK)
            pdf.cell(50, 6, _safe(str(v.get("expected", "-")), 28), border=1, align="R")
            pdf.cell(48, 6, _safe(str(v.get("notes", "")), 30), border=1, new_x="LMARGIN", new_y="NEXT")
    else:
        _note(pdf, "No validation records.")


def _narrative_block(pdf: _PDF, text: str | None, status: str) -> None:
    """Purple left-border block for AI narrative content."""
    if status != "GENERATED" or not text:
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(150, 150, 150)
        pdf.cell(0, 5, "AI narrative: withheld for this section.", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*_DARK)
        pdf.ln(1)
        return

    x0 = pdf.get_x()
    y0 = pdf.get_y()
    pdf.set_font("Helvetica", "I", 8.5)
    pdf.set_text_color(80, 60, 120)
    pdf.set_x(x0 + 4)
    pdf.multi_cell(_EPW - 4, 4.5, _safe(text), new_x="LMARGIN", new_y="NEXT")
    y1 = pdf.get_y()
    pdf.set_draw_color(124, 92, 252)
    pdf.set_line_width(0.8)
    pdf.line(x0, y0, x0, y1)
    pdf.set_line_width(0.2)
    pdf.set_draw_color(200, 200, 200)
    pdf.set_text_color(*_DARK)
    pdf.ln(2)


def _render_narrative_appendix(
    pdf: _PDF,
    narr_out: Any,
) -> None:
    """Section 8 — Narrative Generation Metadata."""
    if narr_out is None:
        return

    pdf.add_page()
    _sec(pdf, "8", "Appendix - AI Narrative Audit")

    _subsec(pdf, "8A", "Narrative Generation Metadata")
    for label, val in [
        ("Model ID", narr_out.model_id),
        ("Prompt Version", narr_out.prompt_version),
        ("Latency (ms)", str(narr_out.latency_ms)),
        ("Input Tokens", str(narr_out.input_tokens)),
        ("Output Tokens", str(narr_out.output_tokens)),
        ("Sections Generated", str(sum(1 for v in narr_out.sections.values() if v.status == "GENERATED"))),
        ("Sections Withheld", str(sum(1 for v in narr_out.sections.values() if v.status == "WITHHELD"))),
    ]:
        _kv(pdf, label, _safe(str(val)[:72]))
    pdf.ln(6)


_GREEN = (0, 128, 64)
_PURPLE = (100, 50, 160)
_LIGHT_BLUE_BG = (235, 243, 255)
_LIGHT_GREEN_BG = (235, 250, 240)

# Field-table layout for 9A agent cards
_CARD_INDENT = 4   # mm from left margin
_FIELD_LW = 46     # label column width
_FIELD_VW = 130    # value column width (4 + 46 + 130 = 180 = _EPW)
_FIELD_H = 5.5     # row height

# Section-header background colours (INPUT / TOOL / OUTPUT / NEXT)
_INPUT_BG = (238, 244, 255)
_TOOL_BG = (238, 250, 238)
_OUTPUT_BG = (232, 252, 238)
_NEXT_BG = (252, 250, 232)


# ---------------------------------------------------------------------------
# Agent card helpers
# ---------------------------------------------------------------------------

def _field_section_hdr(pdf: _PDF, text: str, bg: tuple, fg: tuple = (40, 60, 130)) -> None:
    """Coloured full-width section header row inside an agent card."""
    pdf.set_x(pdf.l_margin + _CARD_INDENT)
    pdf.set_fill_color(*bg)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*fg)
    pdf.cell(_FIELD_LW + _FIELD_VW, _FIELD_H, _safe(f"  {text}"), fill=True, border=1,
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_DARK)


def _field_kv(pdf: _PDF, label: str, value: str, val_color: tuple = _DARK) -> None:
    """One label | value row in a card section (bordered 2-column table row)."""
    pdf.set_x(pdf.l_margin + _CARD_INDENT)
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.set_text_color(*_MUTED)
    pdf.cell(_FIELD_LW, _FIELD_H, _safe(f"  {label}"), border=1)
    pdf.set_font("Helvetica", size=7.5)
    pdf.set_text_color(*val_color)
    pdf.cell(_FIELD_VW, _FIELD_H, _safe(f"  {value}", 78), border=1, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_DARK)


def _agent_card(pdf: _PDF, log_entry: Any) -> None:
    """Full agent card: header + INPUT table + TOOL table(s) + OUTPUT table + NEXT."""
    # Header bar
    pdf.set_fill_color(*_LIGHT_BLUE_BG)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*_ACCENT)
    ms_str = f"{log_entry.duration_ms:,}ms" if log_entry.duration_ms else "<1ms"
    status_marker = "[OK]" if log_entry.status == "COMPLETED" else "[FAIL]"
    pdf.cell(
        0, 7,
        _safe(f"  Agent {log_entry.execution_order}: {log_entry.agent_name}"
              f"   {status_marker}   {ms_str}"),
        fill=True, new_x="LMARGIN", new_y="NEXT",
    )
    pdf.set_text_color(*_DARK)
    pdf.ln(1)

    meta = log_entry.pipeline_meta or {}

    # ── INPUT ──
    input_fields: list[tuple[str, str]] = meta.get("input_fields", [])
    if input_fields:
        _field_section_hdr(pdf, "INPUT", _INPUT_BG)
        for label, value in input_fields:
            _field_kv(pdf, label, value)
        pdf.ln(1)

    # ── TOOL calls ──
    tool_calls: list[dict] = meta.get("tool_calls", [])
    if tool_calls:
        # Group by tool name (preserving insertion order)
        grouped: dict[str, list[dict]] = {}
        for tc in tool_calls:
            grouped.setdefault(tc["tool"], []).append(tc)

        for tool_name, calls in grouped.items():
            if len(calls) == 1:
                tc = calls[0]
                _field_section_hdr(pdf, f"TOOL: {tool_name}", _TOOL_BG, fg=(20, 100, 40))
                for label, value in tc.get("input_fields", [("Passed", tc.get("input", "-"))]):
                    _field_kv(pdf, f"In: {label}", value)
                for label, value in tc.get("output_fields", [("Received", tc.get("output", "-"))]):
                    _field_kv(pdf, f"Out: {label}", value, val_color=_GREEN)
            else:
                # Many same-tool calls (e.g. metric_lookup x14) — show summary
                n = len(calls)
                found_n = sum(1 for c in calls if "FOUND" in c.get("output", ""))
                first_id = calls[0].get("input", "-")
                last_id = calls[-1].get("input", "-")
                _field_section_hdr(pdf, f"TOOL: {tool_name}  ({n} calls)", _TOOL_BG, fg=(20, 100, 40))
                _field_kv(pdf, "In: First ID", first_id)
                _field_kv(pdf, "In: Last ID", last_id)
                _field_kv(pdf, "Out: Found", f"{found_n}/{n}  (see 9C for all values)", val_color=_GREEN)
        pdf.ln(1)
    else:
        _field_section_hdr(pdf, "TOOL: none (pure computation)", _TOOL_BG, fg=(20, 100, 40))
        pdf.ln(1)

    # ── OUTPUT ──
    output_fields: list[tuple[str, str]] = meta.get("output_fields", [])
    if output_fields:
        _field_section_hdr(pdf, "OUTPUT", _OUTPUT_BG, fg=(20, 100, 40))
        for label, value in output_fields:
            _field_kv(pdf, label, value, val_color=_GREEN)
        pdf.ln(1)

    # ── NEXT ──
    feeds = meta.get("feeds_into", "")
    if feeds:
        _field_section_hdr(pdf, f"NEXT:  {feeds}", _NEXT_BG, fg=(80, 60, 20))

    pdf.ln(3)
    pdf.set_draw_color(210, 220, 235)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.set_draw_color(200, 200, 200)
    pdf.ln(3)


def _render_agent_appendix(pdf: _PDF, agent_snapshot: dict) -> None:
    """Section 9 — Agentic Pipeline Audit (9A-9C).

    9A: Agent Execution Audit — one card per agent showing input, tool calls
        with input/response, output, and what it feeds into next.
    9B: Tool Execution Audit  — full table (tool, agent, input, response, ms).
    9C: Pipeline Summary      — agents/tools executed and token usage.
    """
    agent_logs = sorted(
        agent_snapshot.get("agent_logs", []),
        key=lambda a: a.execution_order,
    )
    tool_logs = agent_snapshot.get("tool_logs", [])

    # Index for joining tool_logs to agent names (used in 9B)
    agent_id_to_name: dict = {a.agent_run_id: a.agent_name for a in agent_logs}
    # Group tool_logs by agent_run_id (preserving order) for accurate pairing in 9B
    from collections import defaultdict
    agent_tool_logs: dict = defaultdict(list)
    for tl in tool_logs:
        agent_tool_logs[tl.agent_run_id].append(tl)

    pdf.add_page()
    _sec(pdf, "9", "Appendix - Agentic Pipeline Audit (Phase 2.5)")

    # -----------------------------------------------------------------------
    # 9A — Agent Execution Audit (one card per agent)
    # -----------------------------------------------------------------------
    _subsec(pdf, "9A", "Agent Execution Audit")
    _note(
        pdf,
        "Each card shows: what input the agent received, which tool(s) it called "
        "(with the exact input passed and the response received), the output it produced, "
        "and which agent or stage it feeds into next.",
    )
    if agent_logs:
        for a in agent_logs:
            _agent_card(pdf, a)
    else:
        _note(pdf, "No agent execution records.")
    pdf.ln(2)

    # -----------------------------------------------------------------------
    # 9B — Tool Execution Audit (full table)
    # -----------------------------------------------------------------------
    _subsec(pdf, "9B", "Tool Execution Audit")
    _note(pdf, "Every tool call logged: tool name, which agent called it, input passed, response received, latency.")

    # Build rows from pipeline_meta (clean strings) paired with tool_log timing
    all_tool_rows: list[dict] = []
    for a in agent_logs:
        meta_calls = (a.pipeline_meta or {}).get("tool_calls", [])
        tl_list = agent_tool_logs.get(a.agent_run_id, [])
        for i, tc in enumerate(meta_calls):
            tl = tl_list[i] if i < len(tl_list) else None
            all_tool_rows.append({
                "tool": tc["tool"],
                "agent": a.agent_name,
                "input": tc.get("input", "-"),
                "output": tc.get("output", "-"),
                "status": tl.status if tl else "SUCCESS",
                "duration_ms": tl.duration_ms if tl else None,
            })

    if all_tool_rows:
        # Cols: 32 + 48 + 52 + 36 + 12 = 180
        _th(pdf, [
            ("Tool", 32), ("Agent", 48), ("Input Passed", 52), ("Response Received", 36), ("ms", 12),
        ])
        for row in all_tool_rows:
            status_color = _RED if row["status"] == "FAILED" else _DARK
            ms_str = str(row["duration_ms"]) if row["duration_ms"] is not None else "<1"
            pdf.set_font("Helvetica", size=8)
            pdf.cell(32, 6, _safe(row["tool"], 18), border=1)
            # Shorten agent names that are long
            agent_short = row["agent"].replace("Agent", "").replace("Generation", "Gen")
            pdf.cell(48, 6, _safe(agent_short, 26), border=1)
            pdf.cell(52, 6, _safe(row["input"], 30), border=1)
            pdf.set_text_color(*status_color)
            pdf.cell(36, 6, _safe(row["output"], 22), border=1)
            pdf.set_text_color(*_DARK)
            pdf.cell(12, 6, _safe(ms_str), border=1, align="R", new_x="LMARGIN", new_y="NEXT")
    else:
        _note(pdf, "No tool execution records.")
    pdf.ln(4)

    # -----------------------------------------------------------------------
    # 9C — Pipeline Summary
    # -----------------------------------------------------------------------
    _subsec(pdf, "9C", "Pipeline Summary")

    # Bedrock stats from tool_logs
    bedrock_logs = [t for t in tool_logs if t.tool_name == "bedrock_invoke"]
    bedrock_ms = sum(t.duration_ms or 0 for t in bedrock_logs)

    pipeline_run_id = agent_snapshot.get("pipeline_run_id")
    completed = sum(1 for a in agent_logs if a.status == "COMPLETED")
    failed = sum(1 for a in agent_logs if a.status == "FAILED")

    for label, val in [
        ("Pipeline Run ID", _safe(str(pipeline_run_id or "N/A"), 60)),
        ("Pipeline Version", "2.5.0"),
        ("Agents Executed / Failed", f"{completed} / {failed}"),
        ("Tool Calls Total", str(len(tool_logs))),
        ("  bedrock_invoke calls", str(len(bedrock_logs))),
        ("Bedrock Total Latency (ms)", str(bedrock_ms) if bedrock_ms else "N/A"),
    ]:
        _kv(pdf, _safe(label), _safe(val))


def build_pdf(
    data: ReportData,
    audit_data: dict | None = None,
    narratives: dict[str, str | None] | None = None,
    narrative_output: Any = None,
    agent_snapshot: dict | None = None,
) -> bytes:
    """Render ``data`` into a PDF and return the raw bytes."""
    pdf = _PDF(orientation="P", unit="mm", format="A4")
    pdf._fy = data.fiscal_year
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(15, 20, 15)
    pdf.add_page()

    withheld = {name for name, _ in data.withheld_sections}
    narr = narratives or {}  # {section_key: text | None}

    # ---- Title block ----
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(*_ACCENT)
    pdf.cell(0, 12, "Compensation Cycle Report", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*_DARK)
    pdf.cell(0, 7, f"Fiscal Year {data.fiscal_year}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)
    pdf.set_text_color(*_MUTED)
    pdf.cell(0, 6, f"Scope: {data.scope_label}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Generated: {data.generated_at}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)

    # ---- Section 1: Executive Summary ----
    _sec(pdf, "1", "Executive Summary")
    for label, val in [
        ("Headcount", str(data.headcount)),
        ("Headline Increment %", _fmt_pct(data.avg_increment_pct)),
        ("Total Variable Spend", _fmt_inr(data.total_variable_spend)),
        ("Correction Exposure (headcount)", str(data.correction_headcount)),
        ("Data Quality Score", _fmt_pct(data.data_quality_pct)),
    ]:
        _kv(pdf, label, val)
    if narr:
        _narrative_block(pdf, narr.get("exec_summary"), "GENERATED" if narr.get("exec_summary") else "WITHHELD")
    pdf.ln(4)

    # ---- Section 2: Data Quality ----
    _sec(pdf, "2", "Data Quality")
    if data.withheld_sections:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*_RED)
        pdf.cell(0, 5, "Sections withheld due to insufficient data:", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*_DARK)
        for name, reason in data.withheld_sections:
            pdf.set_font("Helvetica", size=9)
            pdf.multi_cell(0, 5, f"  - {name}: {reason}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
    if data.field_completeness:
        _th(pdf, [("Field", 120), ("Completeness %", 60)])
        for label, pct in data.field_completeness:
            _tr(pdf, [(label, 120), (_fmt_pct(pct), 60)])
    if narr:
        _narrative_block(pdf, narr.get("data_quality_commentary"), "GENERATED" if narr.get("data_quality_commentary") else "WITHHELD")
    pdf.ln(4)

    # ---- Section 3: Retention vs Pay ----
    _sec(pdf, "3", "Retention vs Pay")
    if "Retention vs Pay" in withheld:
        _note(pdf, "Withheld - see Section 2 for reason.")
    else:
        _note(
            pdf,
            "Flagged as correlation only - an increase preceding an exit does not imply pay caused it.",
        )
        # Metric #1: increased-but-quit
        _th(pdf, [("Metric", 115), ("Count", 35), ("% of Headcount", 30)])
        _tr(pdf, [
            ("Received increase, then quit - Metric #1", 115),
            (str(data.increased_and_quit_count), 35),
            (_fmt_pct(data.increased_and_quit_pct), 30),
        ])
        # Metric #2: no-lift band
        _tr(pdf, [
            ("Zero-lift employees (no increment given) - Metric #2", 115),
            (str(data.no_lift_count), 35),
            (_fmt_pct(data.no_lift_pct), 30),
        ])
    if narr:
        _narrative_block(pdf, narr.get("spend_analysis"), "GENERATED" if narr.get("spend_analysis") else "WITHHELD")
    pdf.ln(4)

    # ---- Section 4: Spend & Movement ----
    _sec(pdf, "4", "Spend & Movement")
    for label, val in [
        ("Effective Increment % - Metric #3", _fmt_pct(data.effective_increment_pct)),
        ("Variable Pay - Actual Payout - Metric #4", _fmt_inr(data.variable_payout_actual)),
        ("Variable Pay - Target Payout", _fmt_inr(data.variable_payout_target)),
        ("Variable Pay Attainment %", _fmt_pct(data.variable_payout_attainment_pct)),
        (
            "Promotions - Metric #5",
            f"{data.promotion_count} ({_fmt_pct(data.promotion_pct)} of headcount)",
        ),
    ]:
        _kv(pdf, label, val)
    if narr:
        _narrative_block(pdf, narr.get("promotion_commentary"), "GENERATED" if narr.get("promotion_commentary") else "WITHHELD")
    pdf.ln(4)

    # ---- Section 5: Corrections by Job Family ----
    _sec(pdf, "5", "Corrections by Job Family - Metric #6")
    if "Corrections by Job Family" in withheld:
        _note(pdf, "Withheld - see Section 2 for reason.")
    elif not data.corrections_by_family:
        _note(pdf, "No under-band or under-market employees found in this population.")
    else:
        # Columns: 50 + 14 + 24 + 24 + 36 + 32 = 180
        cols = [
            ("Job Family", 50),
            ("HC", 14),
            ("Under Band", 24),
            ("Under Mkt", 24),
            ("Cost to Close", 36),
            ("Benchmark", 32),
        ]
        _th(pdf, cols)
        for row in data.corrections_by_family:
            _tr(pdf, [
                (str(row["job_family"])[:28], 50),
                (str(row["headcount"]), 14),
                (str(row["under_band"]), 24),
                (str(row["under_market"]), 24),
                (_fmt_inr(row["cost_to_close"]), 36),
                (str(row["benchmark_vintage"])[:20], 32),
            ])
    if narr:
        _narrative_block(pdf, narr.get("correction_commentary"), "GENERATED" if narr.get("correction_commentary") else "WITHHELD")
    pdf.ln(4)

    # ---- Section 6: Equity Participation ----
    _sec(pdf, "6", "Equity Participation - Metric #8")
    pdf.set_font("Helvetica", size=9)
    pdf.cell(
        0, 6,
        f"Total LTI participants: {data.total_lti_participants}",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.ln(2)
    if data.lti_plans:
        _th(pdf, [("LTI Plan / Type", 120), ("Participants", 60)])
        for plan in data.lti_plans:
            _tr(pdf, [(str(plan["lti_type"]), 120), (str(plan["participant_count"]), 60)])
    else:
        _note(pdf, "No LTI-eligible employees in this population.")
    if narr:
        _narrative_block(pdf, narr.get("equity_commentary"), "GENERATED" if narr.get("equity_commentary") else "WITHHELD")
    pdf.ln(4)

    # ---- Section 7: Phase 1 Appendix ----
    if audit_data is not None:
        _render_audit_appendix(pdf, audit_data)
    else:
        pdf.add_page()
        _sec(pdf, "7", "Appendix - Tool-Call Audit Trail")
        _th(pdf, [("Step", 12), ("Function", 48), ("Inputs", 72), ("Output", 30), ("Timestamp", 18)])
        for entry in data.audit_trail:
            _tr(pdf, [
                (str(entry.get("step", "")), 12),
                (str(entry.get("function", ""))[:30], 48),
                (str(entry.get("inputs", ""))[:46], 72),
                (str(entry.get("output", ""))[:20], 30),
                (str(entry.get("timestamp", ""))[:16], 18),
            ], aligns=["C", "L", "L", "L", "L"])

    # ---- Section 8: Phase 2 Narrative Appendix ----
    if narrative_output:
        _render_narrative_appendix(pdf, narrative_output)

    # ---- Section 9: Phase 2.5 Agentic Pipeline Audit ----
    if agent_snapshot:
        _render_agent_appendix(pdf, agent_snapshot)

    return bytes(pdf.output())
