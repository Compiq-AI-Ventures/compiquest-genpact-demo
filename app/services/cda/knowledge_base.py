"""Domain knowledge base injected into the CD&A narrator's prompt.

Two parts, concatenated into one context block:

1. **Bundled reference workbook** — ``data/Genpact_Executive_Summary.xlsx``
   flattened to plain text. This gives the local model concrete, in-domain
   examples of the metrics it will narrate (spend, attrition, compa-ratio…),
   so its prose reasons about them correctly.
2. **Static glossary** — short, plain-language definitions of the acronyms
   the model must never mis-explain (JVRE, TCC, compa-ratio, PSU/RSU…).

The KB is *reference material for reasoning only*. It is never a source of
numbers for the rendered tables — those come exclusively from the uploaded
workbook via ``parser.py``. The narrator is explicitly told (in
``narrative.py``) to use only the figures it is handed and treat the KB as
background.
"""

from __future__ import annotations

import functools
from pathlib import Path

import openpyxl

_KB_PATH = Path(__file__).parent / "data" / "Genpact_Executive_Summary.xlsx"

_GLOSSARY = """\
DOMAIN GLOSSARY (plain-language reference — do not quote verbatim):
- CD&A: Compensation Discussion & Analysis, the proxy-statement section that
  explains how and why executives are paid.
- NEO: Named Executive Officer — the senior executives whose pay is disclosed.
- TCC: Total Cash Compensation (base salary + target/actual cash bonus).
- Base cost: the aggregate fixed salary spend for the active population.
- Compa-ratio: pay relative to the market rate for the role; 1.0x = at market,
  above 1.0x = paid above market, below 1.0x = paid below market. "External"
  compares to the outside market; "internal" compares within the company.
- Attrition rate: the share of employees who left over the period.
- Leadership retention: the share of senior leaders (top job levels) retained.
- Increment: an annual pay increase, quoted as a percentage of base or TCC.
- JVRE: the Job Value Recommendation Engine — the model that produces the
  budget-governed increment recommendations referenced in the summary.
- PSU: Performance Share Unit — equity that vests on performance goals.
- RSU: Restricted Share Unit — equity that vests over time (service-based).
- Say-on-pay: the annual advisory shareholder vote on executive compensation.
"""


@functools.lru_cache(maxsize=1)
def _reference_text() -> str:
    """Flatten the bundled reference workbook to text (cached per process)."""
    if not _KB_PATH.is_file():
        return ""
    try:
        wb = openpyxl.load_workbook(_KB_PATH, data_only=True, read_only=True)
    except Exception:
        return ""
    lines: list[str] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
            if cells:
                lines.append(" | ".join(cells))
    wb.close()
    return "\n".join(lines)


def knowledge_block() -> str:
    """Return the full KB context block for the narrator's prompt."""
    ref = _reference_text()
    parts = [_GLOSSARY]
    if ref:
        parts.append(
            "REFERENCE EXECUTIVE SUMMARY (Genpact F&A — background context "
            "only, NOT the source of this report's numbers):\n" + ref
        )
    return "\n\n".join(parts)
