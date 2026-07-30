"""fpdf2 renderer for the CD&A report.

Consumes a :class:`~app.services.cda.schema.CDADataset` plus a flat list of
content blocks (see ``schema`` for the block DSL) and returns raw PDF bytes.
No data access or LLM calls happen here — everything was resolved upstream.

Uses fpdf2 (>=2.8) core fonts only, so there is nothing to install and no
external font/asset dependency. The reference document's typographic
characters (curly quotes, em dashes, bullet glyphs) are normalised to their
Latin-1 equivalents before rendering so the core fonts never emit tofu.
"""

from __future__ import annotations

from fpdf import FPDF
from fpdf.enums import Align, MethodReturnValue
from fpdf.fonts import FontFace

from .schema import Block, CDADataset

# --- palette ---------------------------------------------------------------
_INK = (22, 22, 22)
_MUTED = (110, 110, 110)
_ACCENT = (192, 57, 43)      # deep coral/red — section rules & values
_ACCENT_DK = (120, 35, 27)
_AMBER = (244, 165, 34)      # #F4A522 — highlight-card headline values
_CREAM = (251, 244, 230)     # #FBF4E6 — highlight-card fill
_LINE = (210, 205, 195)
_ZEBRA = (247, 243, 236)
_HEAD_BG = (243, 233, 214)
_WHITE = (255, 255, 255)

_EPW = 180.0  # A4 (210mm) minus 15mm margins each side

# Unicode → Latin-1 normalisation map for the core fonts.
_SUBS = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "•": "-", "…": "...",
    " ": " ", "−": "-", "●": "-", "₹": "INR ",
    "→": "->", "≤": "<=", "≥": ">=",
}


def _safe(text: str) -> str:
    for k, v in _SUBS.items():
        text = text.replace(k, v)
    return text.encode("latin-1", errors="replace").decode("latin-1")


class _PDF(FPDF):
    company: str = "Genpact"

    def header(self) -> None:
        if self.page_no() == 1:
            return  # cover page draws its own masthead
        self.set_y(10)
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*_MUTED)
        self.cell(0, 6, _safe(f"{self.company} — Compensation Discussion & Analysis"),
                  align="L")
        self.set_draw_color(*_LINE)
        self.line(self.l_margin, 16, self.w - self.r_margin, 16)
        self.set_y(22)  # leave the cursor cleanly below the rule for body content

    def footer(self) -> None:
        self.set_y(-14)
        self.set_font("Helvetica", "I", 7.5)
        self.set_text_color(*_MUTED)
        self.cell(0, 6, _safe(f"Page {self.page_no()}   ·   Confidential — for internal use"),
                  align="C")


# ---------------------------------------------------------------------------
# Block renderers
# ---------------------------------------------------------------------------

def _h1(pdf: _PDF, text: str) -> None:
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 17)
    pdf.set_text_color(*_INK)
    pdf.multi_cell(0, 8, _safe(text), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)


def _h2(pdf: _PDF, text: str) -> None:
    pdf.ln(1)
    pdf.set_font("Helvetica", "B", 12.5)
    pdf.set_text_color(*_ACCENT_DK)
    pdf.multi_cell(0, 6.5, _safe(text), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)


def _h3(pdf: _PDF, text: str) -> None:
    pdf.ln(2.5)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*_INK)
    pdf.multi_cell(0, 6, _safe(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*_ACCENT)
    pdf.set_line_width(0.4)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.l_margin + _EPW, y)
    pdf.set_line_width(0.2)
    pdf.ln(2)


def _p(pdf: _PDF, text: str) -> None:
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*_INK)
    pdf.multi_cell(0, 4.8, _safe(text), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.5)


def _bullets(pdf: _PDF, items: list[str]) -> None:
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*_INK)
    for item in items:
        x = pdf.get_x()
        pdf.cell(5, 4.8, _safe("-"))
        pdf.set_x(x + 5)
        pdf.multi_cell(_EPW - 5, 4.8, _safe(item), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.5)


def _note(pdf: _PDF, text: str) -> None:
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(*_MUTED)
    pdf.multi_cell(0, 4.2, _safe(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_INK)
    pdf.ln(1.5)


def _table(pdf: _PDF, columns: list[str], rows: list[list[str]], numeric_from: int) -> None:
    pdf.ln(0.5)
    pdf.set_draw_color(*_LINE)
    ncol = len(columns)
    aligns = tuple(
        Align.R if (numeric_from is not None and i >= numeric_from and i != 0) else Align.L
        for i in range(ncol)
    )
    with pdf.table(
        borders_layout="SINGLE_TOP_LINE",
        cell_fill_color=_ZEBRA,
        cell_fill_mode="ROWS",
        headings_style=FontFace(emphasis="BOLD", fill_color=_HEAD_BG, color=_INK),
        line_height=5.0,
        text_align=aligns,
        first_row_as_headings=True,
        width=_EPW,
    ) as t:
        pdf.set_font("Helvetica", "", 8.5)
        head = t.row()
        for c in columns:
            head.cell(_safe(c))
        for row in rows:
            r = t.row()
            for c in row:
                r.cell(_safe(str(c)))
    pdf.ln(2.5)


def _cards(pdf: _PDF, cards: list[dict]) -> None:
    """Render the KPI 'Key Metrics' grid as a compact reference table."""
    columns = ["Metric", "Value", "Detail", "Comparison / Change", "Period"]
    rows = [[c.get("metric", ""), c.get("value", ""), c.get("detail", ""),
             c.get("comparison", ""), c.get("period", "")] for c in cards]
    _table(pdf, columns, rows, numeric_from=99)


# stat-card geometry (mm)
_SC_GAP = 4.0        # gutter between the two columns and between rows
_SC_PAD = 4.5        # inner padding inside each card
_SC_VAL_H = 7.0      # line height of the bold headline value
_SC_TXT_H = 4.6      # line height of the description text


def _statcard_body_height(pdf: _PDF, card: dict, inner_w: float) -> float:
    """Measure the rendered height of one card's text (value + description)."""
    pdf.set_font("Helvetica", "B", 15)
    val_lines = pdf.multi_cell(
        inner_w, _SC_VAL_H, _safe(card.get("value", "")),
        dry_run=True, output=MethodReturnValue.LINES,
    )
    pdf.set_font("Helvetica", "", 9)
    txt_lines = pdf.multi_cell(
        inner_w, _SC_TXT_H, _safe(card.get("text", "")),
        dry_run=True, output=MethodReturnValue.LINES,
    )
    return len(val_lines) * _SC_VAL_H + 1.5 + len(txt_lines) * _SC_TXT_H


def _statcards(pdf: _PDF, cards: list[dict]) -> None:
    """A 2-column grid of cream highlight cards (bold amber value + text),
    mirroring the reference template's 'Key Financial Highlights' panel."""
    pdf.ln(1)
    col_w = (_EPW - _SC_GAP) / 2.0
    inner_w = col_w - 2 * _SC_PAD

    for i in range(0, len(cards), 2):
        pair = cards[i:i + 2]
        row_h = max(_statcard_body_height(pdf, c, inner_w) for c in pair) + 2 * _SC_PAD

        # Keep a row intact across page boundaries.
        if pdf.get_y() + row_h > pdf.page_break_trigger:
            pdf.add_page()
        y0 = pdf.get_y()

        for j, card in enumerate(pair):
            x0 = pdf.l_margin + j * (col_w + _SC_GAP)
            pdf.set_fill_color(*_CREAM)
            pdf.rect(x0, y0, col_w, row_h, style="F")

            pdf.set_xy(x0 + _SC_PAD, y0 + _SC_PAD)
            pdf.set_font("Helvetica", "B", 15)
            pdf.set_text_color(*_AMBER)
            pdf.multi_cell(inner_w, _SC_VAL_H, _safe(card.get("value", "")),
                           new_x="LEFT", new_y="NEXT")

            pdf.set_x(x0 + _SC_PAD)
            pdf.ln(1.5)
            pdf.set_x(x0 + _SC_PAD)
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(*_INK)
            pdf.multi_cell(inner_w, _SC_TXT_H, _safe(card.get("text", "")),
                           new_x="LEFT", new_y="NEXT")

        pdf.set_y(y0 + row_h + _SC_GAP)

    pdf.set_text_color(*_INK)
    pdf.ln(1)


def _spacer(pdf: _PDF, mm: float) -> None:
    pdf.ln(mm)


_RENDER = {
    "h1": lambda pdf, b: _h1(pdf, b["text"]),
    "h2": lambda pdf, b: _h2(pdf, b["text"]),
    "h3": lambda pdf, b: _h3(pdf, b["text"]),
    "p": lambda pdf, b: _p(pdf, b["text"]),
    "bullets": lambda pdf, b: _bullets(pdf, b["items"]),
    "note": lambda pdf, b: _note(pdf, b["text"]),
    "table": lambda pdf, b: _table(pdf, b["columns"], b["rows"], b.get("numeric_from", 1)),
    "cards": lambda pdf, b: _cards(pdf, b["cards"]),
    "statcards": lambda pdf, b: _statcards(pdf, b["cards"]),
    "spacer": lambda pdf, b: _spacer(pdf, b.get("mm", 3.0)),
}


# ---------------------------------------------------------------------------
# Cover
# ---------------------------------------------------------------------------

def _cover(pdf: _PDF, ds: CDADataset) -> None:
    pdf.add_page()
    pdf.set_y(60)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*_ACCENT)
    pdf.cell(0, 6, _safe(f"{ds.company.upper()}  ·  {ds.fiscal_year} PROXY STATEMENT"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 26)
    pdf.set_text_color(*_INK)
    pdf.multi_cell(0, 12, _safe("Compensation Discussion\n& Analysis"),
                   new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 13)
    pdf.set_text_color(*_MUTED)
    pdf.cell(0, 8, _safe("Named Executive Officers — Excluding CEO"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_draw_color(*_ACCENT)
    pdf.set_line_width(0.8)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + 60, pdf.get_y())
    pdf.set_line_width(0.2)
    if ds.exec_summary and ds.exec_summary.subtitle:
        pdf.ln(8)
        pdf.set_font("Helvetica", "I", 9.5)
        pdf.set_text_color(*_MUTED)
        pdf.multi_cell(0, 5, _safe(ds.exec_summary.subtitle),
                       new_x="LMARGIN", new_y="NEXT")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_pdf(ds: CDADataset, blocks: list[Block]) -> bytes:
    pdf = _PDF(orientation="P", unit="mm", format="A4")
    pdf.company = ds.company
    pdf.set_margins(15, 20, 15)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_line_width(0.2)

    _cover(pdf, ds)
    pdf.add_page()

    for block in blocks:
        renderer = _RENDER.get(block["type"])
        if renderer is not None:
            renderer(pdf, block)

    out = pdf.output()
    return bytes(out)
