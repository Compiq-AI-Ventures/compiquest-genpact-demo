"""Layers 8 & 9 — hallucination validator and answer validation.

Layer 8, Rule 3 (numeric grounding) is the headline defence: every
monetary number in the narrated answer must trace back to a value in
the context object, or the answer is **blocked** (not hedged). Because
SSE cannot retract emitted tokens, the pipeline buffers the narration
fully, validates here, and only then emits — preserving the guardrail
while keeping the SSE contract.

Threshold rationale: we validate numbers of magnitude >= ``_MIN_MAGNITUDE``
(monetary figures — the real fabrication risk). Small numbers (counts,
single/double-digit percentages, ordinals) are skipped to avoid
false-blocking grounded prose; grounded small numbers pass regardless
because they are in the grounded set anyway.
"""

from __future__ import annotations

import re

# Numbers at or above this magnitude must be grounded. Salaries, bonuses
# and benchmarks live here; percentages / counts / years sit below it.
_MIN_MAGNITUDE = 100

# Integer part must start AND end on a digit, so a trailing comma in prose
# ("19,80,000, up from...") is not captured into the token. Values are
# unaffected (commas are stripped on normalise); only the displayed token
# is cleaner — which matters when it's shown in a report.
_NUMBER_RE = re.compile(r"-?\d(?:[\d,]*\d)?(?:\.\d+)?")


def _normalize(token: str) -> float | None:
    """Parse a matched numeric token (commas stripped) to a float."""
    cleaned = token.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _walk_numbers(obj: object, out: set[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _walk_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _walk_numbers(v, out)
    elif isinstance(obj, str):
        # Numbers embedded in string fields (e.g. record_ids, dates) are
        # also legitimate grounding sources.
        for m in _NUMBER_RE.findall(obj):
            n = _normalize(m)
            if n is not None:
                out.add(n)


def grounded_numbers(context: dict) -> set[float]:
    """Every numeric value reachable in the context object."""
    out: set[float] = set()
    _walk_numbers(context, out)
    return out


# --- Provenance index: value -> where it came from ------------------------
# The verification harness needs to do more than ask "is this number in the
# context?" — it must say WHICH database record backs it. ``grounding_index``
# walks the context tracking the field path and the source record_id of each
# group (from the ``_sources`` list the context builder attaches), so every
# narrated number can be tagged with the exact token it traces back to.
def _index_numbers(
    obj: object, path: str, group: str, sources_map: dict, out: dict[float, list[dict]]
) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.setdefault(float(obj), []).append({"field": path, **sources_map.get(group, {})})
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            _index_numbers(v, f"{path}.{k}" if path else str(k), group, sources_map, out)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _index_numbers(v, f"{path}[{i}]", group, sources_map, out)
    elif isinstance(obj, str):
        for m in _NUMBER_RE.findall(obj):
            n = _normalize(m)
            if n is not None:
                out.setdefault(n, []).append({"field": path, **sources_map.get(group, {})})


def grounding_index(context: dict) -> dict[float, list[dict]]:
    """Map each DB-sourced value to the field(s) and record(s) it came from.

    Returns ``{value: [{field, source, record_id}, ...]}``. This is the
    provenance backbone of the verification step: a narrated number is
    "verified" only if it matches a key here, and the matching entries name
    the exact token (record) that backs it.
    """
    sources_map: dict[str, dict] = {}
    for s in context.get("_sources", []):
        grp = s.get("group")
        if grp:
            sources_map[grp] = {"source": s.get("source"), "record_id": s.get("record_id")}
    # The rationale is a legitimate grounding source but isn't in _sources.
    sources_map.setdefault("rationale", {"source": "rationale_text", "record_id": None})

    out: dict[float, list[dict]] = {}
    for key, val in context.items():
        if key == "_sources":
            continue
        group = key.lstrip("_")  # "_rationale" -> "rationale"
        _index_numbers(val, group, group, sources_map, out)
    return out


def _dedupe_sources(sources: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for s in sources:
        key = (s.get("field"), s.get("record_id"))
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


# Cap on the grounded-number sample recorded in the audit trace, so a
# large context doesn't bloat the (forever) audit row.
_GROUNDED_SAMPLE_CAP = 40


# Cap on per-value matched sources recorded in a comparison row.
_SOURCE_CAP = 3


def _verdict_summary(checked: int, ungrounded: list[str]) -> str:
    if checked == 0:
        return "No monetary figures in the narration to verify; nothing to ground."
    if not ungrounded:
        return (
            f"PASS — all {checked} monetary figure(s) the LLM stated trace back to "
            f"a database record; none were fabricated."
        )
    return (
        f"BLOCK — {len(ungrounded)} of {checked} monetary figure(s) the LLM stated "
        f"({', '.join(ungrounded)}) have no backing database record; answer withheld."
    )


def _compare_token(token: str, index: dict[float, list[dict]]) -> dict | None:
    """Resolve one narrated token to a VERIFIED/UNGROUNDED comparison row,
    or ``None`` if it's below the magnitude threshold / unparseable."""
    n = _normalize(token)
    if n is None or abs(n) < _MIN_MAGNITUDE:
        return None
    matches: list[dict] = []
    for val, sources in index.items():
        if abs(n - val) < 0.5:
            matches.extend(sources)
    matches = _dedupe_sources(matches)[:_SOURCE_CAP]
    return {
        "narrated": token,
        "value": n,
        "tag": "VERIFIED" if matches else "UNGROUNDED",
        "verified": bool(matches),
        "matched_sources": matches,
    }


def validation_report(answer: str, context: dict) -> dict:
    """Per-value verification of the SLM narration against the DB tokens.

    For every high-magnitude number the SLM emitted, this finds the DB
    record(s) that back it and tags the number ``VERIFIED`` (traced to a
    source) or ``UNGROUNDED`` (no DB record supports it — a fabrication).
    The answer passes only if every checked number is VERIFIED; otherwise
    the pipeline blocks it. The returned ``comparisons`` list is the
    audit-grade, value-by-value evidence of that decision.
    """
    index = grounding_index(context)
    comparisons = [
        row
        for token in _NUMBER_RE.findall(answer)
        if (row := _compare_token(token, index)) is not None
    ]
    ungrounded = [c["narrated"] for c in comparisons if not c["verified"]]
    verified_count = sum(1 for c in comparisons if c["verified"])
    return {
        "min_magnitude": _MIN_MAGNITUDE,
        "grounded_record_count": len(index),
        "grounded_sample": sorted(index)[:_GROUNDED_SAMPLE_CAP],
        "narration_figures_checked": len(comparisons),
        "verified_count": verified_count,
        "comparisons": comparisons,
        "ungrounded": ungrounded,
        "ok": not ungrounded,
        "summary": _verdict_summary(len(comparisons), ungrounded),
    }


def validate_numbers(answer: str, context: dict) -> tuple[bool, list[str]]:
    """Return ``(ok, ungrounded)``. ``ok`` is False if any
    high-magnitude number in ``answer`` is absent from ``context``.

    Thin wrapper over :func:`validation_report` for callers that only
    need the verdict.
    """
    report = validation_report(answer, context)
    return report["ok"], report["ungrounded"]
