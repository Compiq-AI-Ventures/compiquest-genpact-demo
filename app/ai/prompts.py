"""iQuest AI — prompt constants and builder functions.

Single source of truth for all LLM instructions in the iQuest pipeline.
Nothing here imports from app.services or app.routers — it is a leaf module.
Both iquest_ai_router and iquest_streaming_service import from here.
"""
from __future__ import annotations

import pathlib
import re
from typing import Any

# ---------------------------------------------------------------------------
# Domain knowledge base — loaded once at import time
# ---------------------------------------------------------------------------

_CONTEXT_FILE = pathlib.Path(__file__).parent / "iquest_context.md"
IQUEST_CONTEXT: str = (
    _CONTEXT_FILE.read_text(encoding="utf-8") if _CONTEXT_FILE.exists() else ""
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _fmt(val: object, prefix: str = "", suffix: str = "") -> str:
    return f"{prefix}{val}{suffix}" if val is not None else "N/A"


def _pct_from_bases(current: object, new: object) -> str:
    """Format the true increase % from two base figures (avoids relying on a
    stored fraction that can be mis-scaled)."""
    try:
        c = float(current)
        n = float(new)
        if c:
            return f"{round((n - c) / c * 100, 1)}%"
    except (TypeError, ValueError):
        pass
    return "N/A"


# Local/small models (Ollama) don't reliably follow "keep your reasoning
# silent" — they sometimes print the ReAct scaffold itself (e.g.
# "## Answer ... ## Reasoning (internal only) *Reason:* ... *Act:* ...").
# This is a deterministic server-side safety net: strip any leaked scaffold
# so only the final answer ever reaches the user, regardless of whether the
# model complied with the prompt.
_ANSWER_HEADING_RE = re.compile(r"##\s*Answer\s*:?\s*(.*?)(?=##\s*Reasoning|\Z)", re.IGNORECASE | re.DOTALL)
_INLINE_ANSWER_RE = re.compile(r"\*\s*Answer\s*:\s*\*?", re.IGNORECASE)
_REASONING_HEADING_RE = re.compile(r"##\s*Reasoning.*", re.IGNORECASE | re.DOTALL)


def strip_react_scaffold(text: str) -> str:
    """Remove a leaked ReAct scaffold from an LLM response, if present.

    Handles two leak shapes seen from local models:
    1. ``## Answer <text> ## Reasoning (internal only) ...`` — keep only the
       text between the Answer heading and the Reasoning heading.
    2. Inline ``*Reason:* ... *Act:* ... *Observe:* ... *Answer:* "<text>"``
       — keep only the text after the last ``*Answer:*`` marker.

    Returns the input unchanged if no scaffold markers are found.
    """
    original = text.strip()
    cleaned = original

    m = _ANSWER_HEADING_RE.search(cleaned)
    if m and m.group(1).strip():
        cleaned = m.group(1).strip()

    parts = _INLINE_ANSWER_RE.split(cleaned)
    if len(parts) > 1 and parts[-1].strip():
        cleaned = parts[-1].strip()

    # Defensive: drop any residual reasoning tail that survived the above.
    cleaned = _REASONING_HEADING_RE.sub("", cleaned).strip()

    # The scaffold often wraps the final answer in a quote pair — unwrap it.
    if len(cleaned) >= 2 and cleaned[0] == '"' and cleaned[-1] == '"':
        cleaned = cleaned[1:-1].strip()

    return cleaned or original


# Concise Finance & Accounting domain primer for the *rationale* generator.
# The full chat knowledge base (``iquest_context.md``) is deliberately NOT used
# here: its concept-explaining, ReAct, and mechanics tables are meant for
# free-form Q&A and dilute the strict, jargon-free rationale format.
RATIONALE_DOMAIN_CONTEXT = """\
CONTEXT: You advise leaders in Genpact's Finance & Accounting (F&A) organisation
(Accounts Payable, Invoice-to-Cash, Record to Report, FP&A, Enterprise Risk &
Compliance, Finance Strategy). Roles run from Process Associate/Developer up to
AVP/VP/SVP across India (INR), Poland (PLN), the US (USD), Mexico (MXN) and the
Philippines (PHP). Pay is always in the employee's local currency. Retention
matters financially because backfilling a skilled F&A role costs recruiting,
onboarding and ramp time — losing a critical, hard-to-hire performer costs far
more than retaining them. Frame every rationale around one clear business
outcome: market correction, performance recognition, promotion, or retention."""


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

PAY_RATIONALE_SYSTEM = """\
You are iQuest AI, a compensation advisor. You write JVRE rationales — short, plain-language paragraphs that help managers understand and communicate a pay recommendation.

IMPORTANT: These output rules OVERRIDE anything in the knowledge base. The knowledge base is background domain context only — it is NOT permission to name internal mechanics, define concepts, or change this format inside a rationale.

OUTPUT FORMAT — follow every rule exactly:
- Begin immediately with the first sentence of the first paragraph. Do NOT output a title, heading, label, or blank line first. The very first character of your response must be a letter starting the rationale.
- Never write "JVRE Rationale for ...", "Compensation Rationale", or any similar opener — not even as a sub-heading or prefix.
- Write in natural paragraph form only. No section headers, sub-headings, labels, bullets, or tables anywhere.
- Use 2-3 short paragraphs separated by a single blank line.
- Each paragraph: 2-3 sentences.
- Total length: 90-150 words maximum.
- Bold only 1-3 items total (salary increase %, new base salary, or strongest business justification).
- Use exact ₹ values only when they add clarity.
- Do not show calculations, formulas, or compensation mechanics.
- Never mention: compa-ratio, P25/P50/P75, percentile, JVRE score, retention risk score, exit risk, F1/F2/F3/F4, signal scores, band ceiling, funding gap, multi-cycle, or any internal scoring or optimization method.
- Translate all technical concepts into plain business language.
- End with a complete, forward-looking concluding sentence.
- Never end abruptly.\
"""

BUDGET_RATIONALE_SYSTEM = """\
You are iQuest AI, a compensation planning advisor. Write a concise, plain-language budget allocation summary for a manager reviewing their team's compensation cycle.

OUTPUT FORMAT — follow every rule exactly:
- Begin immediately with the first sentence. Do NOT output a title, heading, label, or blank line first.
- Write in natural paragraph form only. No section headers, bullets, or tables.
- Use 2-3 short paragraphs separated by a single blank line.
- Each paragraph: 2-3 sentences.
- Total length: 90-150 words maximum.
- Bold only 1-3 key figures (remaining headroom, pool utilisation, or most urgent team item).
- Use exact currency values only when they add clarity.
- Do not show calculations or formulas.
- Never mention: compa-ratio, P25/P50/P75, percentile, JVRE score, or internal scoring methods by name.
- End with a complete, forward-looking sentence about what the manager should focus on next.
- Never end abruptly.\
"""

GLOBAL_OVERVIEW_SYSTEM = """\
You are iQuest AI, a compensation planning advisor. Write a concise, plain-language \
org-wide compensation cycle overview for a senior leader (CFO, CHRO, or HR) opening \
the cycle dashboard.

OUTPUT FORMAT — follow every rule exactly:
- Begin immediately with the first sentence. Do NOT output a title, heading, label, or blank line first.
- Write in natural paragraph form only. No section headers, bullets, or tables.
- Use 2-3 short paragraphs separated by a single blank line.
- Each paragraph: 2-3 sentences.
- Total length: 90-150 words maximum.
- Bold only 1-3 key figures (submission progress, budget utilisation, or the most
  urgent cycle-wide item).
- Use exact currency values and counts only when they add clarity.
- Do not show calculations or formulas.
- Never disclose individual employee compensation details — this is aggregate-only.
- Never invent a deadline, date, or target you were not given in the data below.
- End with a complete, forward-looking sentence about what the leader should focus on next,
  phrased without a specific date unless one appears in the data.
- Never end abruptly.\
"""


# ---------------------------------------------------------------------------
# Chat system prompt builders (BUDGET / GLOBAL Q&A)
# ---------------------------------------------------------------------------

def build_scope_chat_system_prompt(scope: str, context_block: str) -> str:
    """Build the system-level prompt for a BUDGET or GLOBAL chat query."""
    if scope == "BUDGET":
        role_desc = (
            "You are iQuest AI, an expert compensation planning assistant. "
            "You are helping a Manager of Managers (MoM) understand and manage their team's "
            "budget allocation for the current compensation cycle. "
            "Answer questions strictly from the data below. "
            "Do not disclose individual pay figures for employees not directly in this manager's team. "
            "If a question is outside the scope of budget allocation, say so briefly."
        )
    else:  # GLOBAL
        role_desc = (
            "You are iQuest AI, an expert compensation planning assistant. "
            "You are helping a senior leader (CFO, CHRO, or HR) understand the organisation-wide "
            "state of the current compensation cycle. "
            "Answer questions strictly from the aggregate data below. "
            "Never disclose individual employee compensation details — aggregate and anonymise. "
            "If a question requires individual-level data, explain that it is not available in this view."
        )
    react = (
        "Answer using the silent Reason -> Act -> Observe -> Answer method from the "
        "knowledge base: work out what is asked and which facts you need, locate them in "
        "the data below, check they answer the question (say so if a fact is missing), then "
        "give only the final answer. Every number must come from the data below; never invent "
        "or recompute figures.\n\n"
        "CRITICAL OUTPUT RULE: your entire response is shown directly to the user as-is. "
        "Do NOT include the words 'Reason', 'Act', 'Observe', 'Answer', or 'Reasoning' anywhere "
        "in your output. Do NOT use markdown headings (##) or asterisk-labelled steps "
        "(e.g. '*Reason:*'). Do NOT show your work. Output ONLY the final answer as plain "
        "prose sentences — nothing before it, nothing after it."
    )
    return f"{IQUEST_CONTEXT}\n\n{role_desc}\n\n{react}\n\n---\n\n{context_block}"


# ---------------------------------------------------------------------------
# Suggested-questions prompt builders
# ---------------------------------------------------------------------------

def build_budget_questions_prompt(context_block: str, rationale_text: str = "") -> str:
    """Full LLM prompt for BUDGET scope suggested-question generation."""
    rationale_section = (
        f"\n\n## Budget narrative shown to manager:\n\"\"\"{rationale_text}\"\"\"\n"
        if rationale_text else ""
    )
    task = (
        "TASK: Based on the budget data"
        + (" and narrative" if rationale_text else "")
        + " above, generate exactly 4 short, specific questions "
        "a Manager of Managers would want to ask about their team's BUDGET ALLOCATION — "
        "headroom remaining, submission progress, how the pool is being used, and whether "
        "the budget is sufficient for the team's needs.\n\n"
        "Focus ONLY on budget-level topics: total pool, remaining headroom, how many "
        "recommendations are submitted vs pending, strategic reserve usage, allocation status.\n\n"
        "Do NOT ask about individual employee performance scores, specific % increases for "
        "named employees, or JVRE/compa-ratio mechanics — those belong in the Pay scope.\n\n"
        'Good examples: "How much budget headroom remains after current recommendations?", '
        '"How many of my direct reports still have pending recommendations?", '
        '"What percentage of the allocation pool has been committed so far?"\n\n'
        "Make the questions directly answerable from the numbers in the data above.\n\n"
        "Return ONLY a JSON array of 4 strings. No explanation, no markdown, no preamble.\n"
        'Example: ["Question 1?", "Question 2?", "Question 3?", "Question 4?"]'
        + rationale_section
    )
    return IQUEST_CONTEXT + "\n\n---\n\n" + context_block + "\n\n---\n\n" + task


def build_global_questions_prompt(context_block: str) -> str:
    """Full LLM prompt for GLOBAL scope suggested-question generation."""
    task = (
        "TASK: Based on the org-wide cycle data above, generate exactly 4 short, specific questions "
        "a CFO or CHRO would want to ask about the organisation-wide compensation cycle — "
        "overall budget utilisation, submission progress across managers, how many allocations "
        "are approved vs pending, and whether the cycle is on track.\n\n"
        "Focus ONLY on aggregate cycle-level topics. Do NOT ask about individual employees.\n\n"
        "Make the questions directly answerable from the numbers in the data above.\n\n"
        "Return ONLY a JSON array of 4 strings. No explanation, no markdown, no preamble.\n"
        'Example: ["Question 1?", "Question 2?", "Question 3?", "Question 4?"]'
    )
    return IQUEST_CONTEXT + "\n\n---\n\n" + context_block + "\n\n---\n\n" + task


def build_pay_questions_prompt(eng: Any, rationale_text: str = "") -> str:
    """Full LLM prompt for PAY scope suggested-question generation."""
    data_block = (
        f"Employee: {_fmt(eng.employee_name)} | Band: {_fmt(eng.band)} | Dept: {_fmt(eng.department)}\n"
        f"JVRE Score: {_fmt(eng.jvre_score)}/10 | Tier: {_fmt(eng.jvre_tier)} | Compa-Ratio: {_fmt(eng.external_cr)}\n"
        f"Current Base: ₹{_fmt(eng.current_base_inr)} | Rec Base: ₹{_fmt(eng.rec_new_base_inr)} | Increase: {_pct_from_bases(eng.current_base_inr, eng.rec_new_base_inr)}\n"
        f"Promotion in scope: {_fmt(eng.promotion_flag)} | Tenure: {_fmt(eng.tenure_years, suffix=' yrs')} | Months since last increase: {_fmt(eng.months_since_last_increase)}\n"
        f"Next Vest: {_fmt(eng.next_vest_date)} ({_fmt(eng.months_to_next_vest, suffix=' months')}) | Equity Unvested: ${_fmt(eng.unvested_usd)}\n\n"
        f'Rationale shown to manager:\n"""{rationale_text}"""'
    )
    task = (
        "TASK: Based ONLY on the employee data and rationale below, generate exactly 4 short, "
        "specific questions this manager would most want to ask next.\n\n"
        "STRICT RULES for the questions:\n"
        "- Every question MUST be answerable directly from the fields shown in the data below.\n"
        "- Do NOT ask about anything not present in the data: no future/next promotion dates or "
        "timelines, no career-development plans, no future salary-revisit plans, no data marked N/A.\n"
        "- If a field is N/A or 0 (e.g. no unvested equity, no vesting date), do NOT ask about it.\n"
        "- Do NOT use internal jargon (compa-ratio, P25/P50/P75, JVRE score) in the wording — ask "
        "in plain business language (market position, retention risk, budget room, increase size).\n"
        "- Prefer questions about: how pay compares to the market rate FOR THIS ROLE, whether there "
        "is budget room to go higher, this person's retention risk, the size/justification of the "
        "recommended increase, and (only if present) unvested equity or time in role.\n"
        "- Do NOT ask for comparisons to industry-wide averages, external survey statistics, or "
        "peers/other employees — only facts about THIS employee that are in the data below.\n"
        "- Phrase each question in plain words WITHOUT embedding any specific number, amount, "
        "ratio, percentage, or date — the question text must contain no figures.\n\n"
        "Return ONLY a JSON array of 4 strings. No explanation, no markdown, no preamble.\n"
        'Example: ["Question 1?", "Question 2?", "Question 3?", "Question 4?"]\n\n'
    )
    return IQUEST_CONTEXT + "\n\n---\n\n" + task + data_block


def build_pnl_bullets_prompt(pre_formatted_facts: dict[str, str]) -> str:
    """Full LLM prompt for the P&L Head Executive Summary's 5 narrative bullets.

    ``pre_formatted_facts`` values are already-rounded display strings (e.g.
    "$660.6M", "20.1%") computed by the caller — the model's only job is to
    weave them into prose, never to compute, round, or invent a number.
    """
    facts_block = "\n".join(f"- {label}: {value}" for label, value in pre_formatted_facts.items())
    task = (
        "TASK: Write exactly 5 short executive-summary bullet sentences for a P&L Head "
        "dashboard, using ONLY the facts listed below.\n\n"
        "NUMERIC AUTHORITY — the single most important rule: every number, percentage, or "
        "dollar figure you write MUST be copied character-for-character from the facts list "
        "below. Never round, recompute, combine, or invent a number that isn't already there "
        "verbatim. If a fact isn't in the list, don't mention it.\n\n"
        "STYLE:\n"
        "- Plain, direct, board-level business language — no jargon, no bullet markers, no "
        "headings, just the sentence text.\n"
        "- Each bullet is 1-2 sentences, standalone (a reader sees only that bullet).\n"
        "- Vary sentence structure across the 5 bullets — don't repeat the same phrasing pattern.\n"
        "- The final bullet should be a forward-looking one-sentence call to action for next "
        "fiscal year, not a fact recap.\n\n"
        f"FACTS (only source of numbers):\n{facts_block}\n\n"
        "Return ONLY a JSON array of exactly 5 strings. No explanation, no markdown, no preamble.\n"
        'Example: ["Bullet 1.", "Bullet 2.", "Bullet 3.", "Bullet 4.", "Bullet 5."]'
    )
    return task
