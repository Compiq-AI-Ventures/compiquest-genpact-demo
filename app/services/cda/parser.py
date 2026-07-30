"""Deterministic parser for the uploaded NEO-compensation file.

Accepts an **.xlsx/.xlsm workbook, a .docx document, or a .pdf** carrying a
per-executive compensation table and extracts one validated
:class:`~app.services.cda.schema.NEO` per row. No LLM is involved; every
number that reaches the report originates here.

Expected columns (matched by **keyword**, so header wording, casing, order,
and extra columns are all tolerated — e.g. the sample's "Total Anual Target
Comp" typo still parses)::

    Executive | Base Salary | Annual Bonus | PSU | RSU | [Total ...]

Derivations enforced here (see the report's "numbers are never invented"
principle):

* **LTI** (long-term incentive) = ``PSU + RSU``.
* **Total annual target compensation** = ``Base + Bonus + PSU + RSU``. If the
  file carries its own total column it is *validated* against this identity
  and a mismatch raises :class:`CDAParseError` naming the offending executive.

Columns the file does not carry (2023 figures, actual bonus payments, PSU/RSU
share counts, titles) are left blank on the :class:`NEO` and render as an
em-dash in the report. Only the roster/numbers the file actually supplies are
shown — nothing is back-filled with stale template values.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any
from xml.etree import ElementTree as ET

import openpyxl

from .schema import NEO

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# First-name → (canonical full name, title) enrichment for the known Genpact
# NEOs. Best-effort only: the displayed name always comes from the file (the
# roster is dynamic); this just supplies a title when the file's first name
# matches a known officer. Unknown names render with a blank title.
_KNOWN_TITLES: dict[str, str] = {
    "michael": "Senior Vice President, Chief Financial Officer",
    "piyush": "Senior Vice President, Chief Human Resources Officer and Country Manager, India",
    "anil": "Senior Vice President and Global Business Leader, Consumer & Healthcare and High Tech & Manufacturing",
    "riju": "Senior Vice President, Chief Growth Officer and Global Business Leader, Enterprise Services and Partnerships and Alliances",
}


class CDAParseError(ValueError):
    """Raised when the uploaded file cannot be parsed into report data."""


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _clean(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _num(cell: str) -> float | None:
    """Parse a currency/number cell ("$650,000", "650000", "2,000,750") → float.

    Returns ``None`` for an empty or non-numeric cell.
    """
    s = _clean(cell)
    if not s:
        return None
    s = re.sub(r"[^0-9.\-]", "", s)
    if s in ("", "-", ".", "-."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _money(n: float) -> str:
    return f"${n:,.0f}"


def _plain(n: float) -> str:
    return f"{n:,.0f}"


# ---------------------------------------------------------------------------
# Format detection + dispatch
# ---------------------------------------------------------------------------

def _detect_format(content: bytes, filename: str) -> str:
    if content[:5] == b"%PDF-":
        return "pdf"
    if content[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                names = set(z.namelist())
            if "word/document.xml" in names:
                return "docx"
            if "xl/workbook.xml" in names or any(n.startswith("xl/") for n in names):
                return "xlsx"
        except zipfile.BadZipFile:
            pass
    low = filename.lower()
    if low.endswith((".xlsx", ".xlsm")):
        return "xlsx"
    if low.endswith(".docx"):
        return "docx"
    if low.endswith(".pdf"):
        return "pdf"
    return "unknown"


def parse_upload(content: bytes, filename: str = "") -> list[NEO]:
    """Parse an uploaded .xlsx/.docx/.pdf into a list of :class:`NEO`.

    Raises :class:`CDAParseError` on an unreadable/unsupported file or one that
    yields no recognisable compensation table.
    """
    fmt = _detect_format(content, filename)
    if fmt == "xlsx":
        grid = _grid_xlsx(content)
    elif fmt == "docx":
        grid = _grid_docx(content)
    elif fmt == "pdf":
        grid = _grid_pdf(content)
    else:
        raise CDAParseError(
            "Unsupported file type. Please upload an .xlsx workbook, a .docx "
            "document, or a .pdf containing the executive compensation table."
        )
    return _neos_from_grid(grid)


# ---------------------------------------------------------------------------
# Per-format grid extraction (each returns a list[list[str]] of cell text)
# ---------------------------------------------------------------------------

def _grid_xlsx(content: bytes) -> list[list[str]]:
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except Exception as exc:
        raise CDAParseError(f"Could not open the uploaded workbook: {exc}") from exc
    ws = wb.active
    if ws is None:
        wb.close()
        raise CDAParseError("The uploaded workbook has no active worksheet.")
    grid = [[_clean(c) for c in row] for row in ws.iter_rows(values_only=True)]
    wb.close()
    return grid


def _grid_docx(content: bytes) -> list[list[str]]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise CDAParseError(f"Could not read the .docx document: {exc}") from exc

    body = ET.fromstring(xml).find(f"{_W_NS}body")
    if body is None:
        raise CDAParseError("The .docx document has no body content.")

    def _p_text(p: ET.Element) -> str:
        return "".join(t.text or "" for t in p.iter(f"{_W_NS}t")).strip()

    def _tbl_grid(tbl: ET.Element) -> list[list[str]]:
        rows: list[list[str]] = []
        for tr in tbl.findall(f"{_W_NS}tr"):
            rows.append([
                "".join(_p_text(p) for p in tc.findall(f"{_W_NS}p")).strip()
                for tc in tr.findall(f"{_W_NS}tc")
            ])
        return rows

    tables = [_tbl_grid(tbl) for tbl in body.iter(f"{_W_NS}tbl")]
    # Prefer the first table whose header row looks like the comp table.
    for grid in tables:
        if grid and _match_columns(grid[0]) is not None:
            return grid
    if tables:
        return tables[0]
    raise CDAParseError("No table was found in the .docx document.")


def _grid_pdf(content: bytes) -> list[list[str]]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(content))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        raise CDAParseError(f"Could not read the PDF: {exc}") from exc

    grid: list[list[str]] = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln:
            grid.append([c.strip() for c in re.split(r"\s{2,}", ln) if c.strip()])
    if not grid:
        raise CDAParseError(
            "No extractable text found in the PDF (it may be a scanned image)."
        )
    return grid


# ---------------------------------------------------------------------------
# Grid → NEOs
# ---------------------------------------------------------------------------

# Column key → header keywords that identify it (first match wins per column).
_COLUMN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "name": ("executive", "officer", "name"),
    "base": ("base",),
    "bonus": ("bonus",),
    "psu": ("psu",),
    "rsu": ("rsu",),
    "total": ("total",),
}
_REQUIRED_COLUMNS = ("base", "bonus", "psu", "rsu")


def _match_columns(header: list[str]) -> dict[str, int] | None:
    """Map a header row to column indices, or ``None`` if it is not the
    compensation-table header. Requires base, bonus, PSU and RSU columns."""
    cols: dict[str, int] = {}
    for i, cell in enumerate(header):
        h = cell.lower()
        for key, keywords in _COLUMN_KEYWORDS.items():
            if key not in cols and any(kw in h for kw in keywords):
                cols[key] = i
    if all(k in cols for k in _REQUIRED_COLUMNS):
        cols.setdefault("name", 0)
        return cols
    return None


def _neo_from_row(row: list[str], cols: dict[str, int]) -> NEO | None:
    """Build one NEO from a data row, or ``None`` if the row is not a usable
    executive row (blank, or missing a required figure). Raises
    :class:`CDAParseError` if the row carries a total that fails the identity
    Total = Base + Bonus + PSU + RSU."""
    def cell(key: str) -> str:
        i = cols.get(key)
        return row[i] if i is not None and i < len(row) else ""

    name = _clean(cell("name"))
    base, bonus, psu, rsu = (_num(cell(k)) for k in ("base", "bonus", "psu", "rsu"))
    if not name or None in (base, bonus, psu, rsu):
        return None

    lti = psu + rsu
    total = base + bonus + lti
    file_total = _num(cell("total"))
    if file_total is not None and abs(file_total - total) > 1.0:
        raise CDAParseError(
            f"{name}: the row's total ({file_total:,.0f}) does not equal "
            f"Base + Annual Bonus + PSU + RSU ({total:,.0f}). Please check the "
            f"uploaded figures."
        )

    return NEO(
        name=name,
        title=_KNOWN_TITLES.get(name.split()[0].lower(), ""),
        base_current=_money(base),
        target_bonus=_money(bonus),
        # LTI = PSU + RSU. Shown with a "$" in the Total Annual Target
        # Compensation table and plain in the equity table (matching the
        # template's two formatting conventions).
        annual_lti_target=_money(lti),
        lti_target_value=_plain(lti),
        total_target_comp=_money(total),
    )


def _neos_from_grid(grid: list[list[str]]) -> list[NEO]:
    header_idx = next(
        (i for i, row in enumerate(grid) if _match_columns(row) is not None), None
    )
    if header_idx is None:
        raise CDAParseError(
            "Could not find the executive compensation table. Expected a header "
            "row with columns for the executive plus Base Salary, Annual Bonus, "
            "PSU and RSU."
        )
    cols = _match_columns(grid[header_idx])
    assert cols is not None  # guaranteed by the search above

    neos: list[NEO] = []
    for row in grid[header_idx + 1:]:
        if not any(_clean(c) for c in row):
            continue  # skip blank spacer rows
        neo = _neo_from_row(row, cols)
        if neo is not None:
            neos.append(neo)
        elif neos:
            break  # a trailing note after the last executive row — table ended

    if not neos:
        raise CDAParseError(
            "The compensation table header was found but no executive rows could "
            "be read from it."
        )
    return neos
