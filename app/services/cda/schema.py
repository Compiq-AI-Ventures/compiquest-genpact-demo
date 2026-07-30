"""Typed data model for the CD&A (Compensation Discussion & Analysis) report.

Design principle (inherited from the reference build): **the LLM never
authors a number or a layout decision.** Everything numeric on the page is
parsed deterministically from the uploaded workbook (see ``parser.py``) or
falls back to a fixed template default (see ``template.py``). This module is
the single source of truth for the *shape* of that data plus a light set of
validators that catch data-quality problems before rendering.

Two kinds of object live here:

* **Data models** (``KpiCard``, ``ExecSummary``, ``NEO``, ``CDADataset``) —
  the validated payload the report is built from.
* **Content blocks** — a tiny plain-dict DSL (``h1``/``p``/``table``/…) that
  the fpdf2 builder walks. Sections in ``template.py`` and the assembled
  document in ``__init__.py`` are just ordered lists of these blocks. Kept as
  dicts (not Pydantic) so building a document stays terse and cheap.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class KpiCard(BaseModel):
    """One row of the 'Key Metrics — Card Data' table in the exec summary."""

    metric: str
    value: str
    detail: str = ""
    comparison: str = ""
    period: str = ""


class ExecSummary(BaseModel):
    """The data-driven executive-summary section, sourced from the upload."""

    title: str = "Executive Summary"
    subtitle: str = ""
    bullets: list[str] = Field(default_factory=list)
    cards: list[KpiCard] = Field(default_factory=list)
    note: str = ""
    # LLM-generated synthesis paragraph, grounded in the cards + KB. Empty
    # when narration is disabled or the model was unreachable.
    narrative: str = ""


class NEO(BaseModel):
    """A Named Executive Officer row for the proxy-style comp tables.

    These figures are not present in the aggregate exec-summary upload, so
    they default to the template's baked (public-proxy) values and are only
    overridden when a NEO-level upload supplies them.
    """

    name: str
    title: str = ""
    base_prior: str = ""
    base_current: str = ""
    bonus_prior: str = ""
    target_bonus: str = ""
    bonus_current: str = ""
    # Total LTI incl. one-time retention grants (the 'Equity-Based Comp' table).
    lti_target_value: str = ""
    psu_target_shares: str = ""
    rsu_count: str = ""
    # Annual-only LTI, used by the 'Total Annual Target Compensation' table.
    annual_lti_target: str = ""
    total_target_comp: str = ""


class CDADataset(BaseModel):
    """The complete, validated payload for one CD&A report."""

    company: str = "Genpact"
    fiscal_year: int = 2024
    # The proxy-statement year printed on the cover masthead and running
    # footer (a proxy statement is filed the year after the fiscal year it
    # covers).
    proxy_year: int = 2026
    # The report is driven entirely by the per-NEO compensation table parsed
    # from the upload; the exec-summary section is no longer part of the
    # template, so it is optional and unused by the current pipeline.
    exec_summary: ExecSummary | None = None
    neos: list[NEO] = Field(default_factory=list)
    # Optional per-field numeric overrides pulled from the upload, keyed by a
    # stable token the template references (e.g. "say_on_pay_pct"). Anything
    # absent falls back to the template default.
    overrides: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _sanity(self) -> CDADataset:
        # The uploaded file is the sole source of the report's numbers, so at
        # least one named executive officer must have been parsed from it or
        # the report would be empty.
        if not self.neos:
            raise ValueError(
                "No named executive officers were parsed from the uploaded "
                "file — the compensation table could not be read."
            )
        return self


# ---------------------------------------------------------------------------
# Content-block DSL — the fpdf2 builder walks a flat list of these.
# ---------------------------------------------------------------------------

Block = dict[str, Any]


def h1(text: str) -> Block:
    return {"type": "h1", "text": text}


def h2(text: str) -> Block:
    return {"type": "h2", "text": text}


def h3(text: str) -> Block:
    return {"type": "h3", "text": text}


def p(text: str) -> Block:
    return {"type": "p", "text": text}


def bullets(items: list[str]) -> Block:
    return {"type": "bullets", "items": list(items)}


def table(columns: list[str], rows: list[list[str]], *, numeric_from: int = 1) -> Block:
    """A bordered table. ``numeric_from`` right-aligns columns at/after index."""
    return {
        "type": "table",
        "columns": list(columns),
        "rows": [[str(c) for c in row] for row in rows],
        "numeric_from": numeric_from,
    }


def cards(items: list[KpiCard]) -> Block:
    """KPI stat cards (the exec-summary 'Key Metrics' grid)."""
    return {"type": "cards", "cards": [c.model_dump() for c in items]}


def stat_cards(items: list[tuple[str, str]]) -> Block:
    """A 2-column grid of highlight cards, each a big bold headline value over
    a description, on a cream fill — mirrors the reference template's "Key
    Financial Highlights" panel. ``items`` are ``(value, description)`` pairs."""
    return {"type": "statcards", "cards": [{"value": v, "text": t} for v, t in items]}


def note(text: str) -> Block:
    return {"type": "note", "text": text}


def spacer(mm: float = 3.0) -> Block:
    return {"type": "spacer", "mm": mm}
