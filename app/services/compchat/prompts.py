"""Layer 8 (Rule 1/2) — the grounded narration prompt + terminal messages.

The narrator is given the JSON context object and nothing else. The
system prompt forbids inventing numbers, requires "I don't have
sufficient information" for missing facts, and asks for plain language.
Terminal messages are the structured, non-LLM responses returned on a
hard stop (ambiguity, denial, out-of-scope, missing data, validation
failure) — these are streamed verbatim and never pass through the model.
"""

from __future__ import annotations

import json

from app.ai.prompts import IQUEST_CONTEXT

from .schemas import ResolvedSubject

_GROUNDED_SYSTEM = """\
You are iQuest AI, a Finance & Accounting compensation advisor for managers.
Answer the manager's question about the employee described in the CONTEXT.

Use a silent Reason -> Act -> Observe -> Answer method (the ReAct flow):
first work out what is being asked and which facts you need; locate those
facts in the CONTEXT; check they actually answer the question and note any
that are missing; then give ONLY the final answer. Never print the reasoning
steps or a scratchpad — the manager sees only the answer.

CRITICAL OUTPUT RULE: your entire response is shown directly to the manager
as-is. Do NOT include the words "Reason", "Act", "Observe", "Answer", or
"Reasoning" anywhere in your output. Do NOT use markdown headings (##) or
asterisk-labelled steps (e.g. "*Reason:*"). Output ONLY the final answer as
plain prose — nothing before it, nothing after it.

Rules (follow without exception):

1. NUMBERS come only from the CONTEXT. Every figure you state must appear
   verbatim in the CONTEXT JSON. Never invent, estimate, recompute, or round
   a number. If a needed number is absent or null, say exactly:
   "I don't have sufficient information to answer that." You MAY use the
   domain knowledge in the KNOWLEDGE BASE above to interpret and explain what
   the numbers mean and why they matter in Finance & Accounting — but the
   values themselves always come from the CONTEXT.
2. CURRENCY: state numbers with the exact digits from the CONTEXT and plain
   thousands-comma grouping (e.g. 3,338,800) — never re-group or round.
   Prefix with the currency shown alongside the number (``currency: "USD"``
   -> "$3,338,800"; ``"INR"`` -> "₹3,338,800").
3. Be concise and lead with the business "why". Do not output JSON, markdown
   headings, or restate the whole record — answer the question directly.
4. Distinguish base-pay figures precisely: ``base_pay.current`` is today's
   base, ``base_pay.jvre_recommended`` is the JVRE engine's recommendation,
   ``base_pay.manager_recommended`` is the manager's, and
   ``base_pay.mom_recommended`` is the manager-of-managers'. Never call one of
   these by another's name. If a requested figure is null, say you don't have it.
5. Affordability vs competitiveness: for "is there room to increase pay", use
   ``budget_headroom.remaining_headroom`` (the manager's uncommitted budget
   pool) if present and non-null. For "is this competitive vs market", use the
   market-benchmark / compa signals instead. Do not confuse the two; if
   ``budget_headroom`` is absent, say you don't have enough info on budget room.
6. If the manager asks you to define a compensation concept (compa-ratio,
   TCC, LTI, JVRE, etc.), explain it plainly using the knowledge base.
7. Do NOT compute and state new numbers — no differences/gaps between two
   figures, no sums, no new totals, no percentages you calculate yourself.
   Describe relationships in WORDS instead ("above the market midpoint",
   "a larger increase than last cycle") and only quote figures that appear
   verbatim in the CONTEXT. This keeps every number you state grounded.
"""


def build_narration_prompt(
    question: str,
    context: dict,
    rationale_text: str | None,
    history: list[tuple[str, str]] | None = None,
) -> str:
    """Assemble the full single-prompt narration input for the LLM.

    ``history`` is the recent (role, content) turns before this question,
    so the narrator can resolve follow-ups and corrections ("that's the
    manager rec, not the JVRE") against the same grounded context.
    """
    parts = []
    if IQUEST_CONTEXT:
        parts.append(
            "=== KNOWLEDGE BASE (Finance & Accounting domain knowledge; use it to "
            "interpret — never as a source of numbers) ===\n" + IQUEST_CONTEXT + "\n\n"
        )
    parts.append(_GROUNDED_SYSTEM)
    parts.append("\n=== CONTEXT (the only facts you may use for numbers) ===\n")
    parts.append(json.dumps(context, ensure_ascii=False, default=str, indent=2))
    if rationale_text:
        parts.append(
            "\n=== RATIONALE ALREADY SHOWN TO THE MANAGER (background; its "
            "numbers are also grounded) ===\n" + rationale_text
        )
    if history:
        turns = "\n".join(f"{role}: {content}" for role, content in history[-6:])
        parts.append("\n=== RECENT CONVERSATION (for context only) ===\n" + turns)
    parts.append(f'\n=== MANAGER QUESTION ===\n{question.strip()}\n\n=== ANSWER ===\n')
    return "".join(parts)


# ---------------------------------------------------------------------------
# Terminal (non-LLM) messages
# ---------------------------------------------------------------------------
def ambiguous(candidates: tuple[ResolvedSubject, ...]) -> str:
    names = "; ".join(f"{c.name} ({c.employee_id})" for c in candidates if c.name)
    return (
        "I found more than one person matching that name. Which did you mean? "
        f"{names}"
    )


def not_found(name: str | None = None) -> str:
    who = f' "{name}"' if name else ""
    return f"I couldn't find an employee{who} you have access to."


def denied(reason: str) -> str:
    return f"You don't have access to that information. {reason}".strip()


def out_of_scope() -> str:
    return (
        "I can only answer questions about your team's compensation, "
        "performance, promotions, org structure, comparisons, and team "
        "analytics. Could you rephrase along those lines?"
    )


def data_unavailable() -> str:
    return "I don't have sufficient information to answer that — the underlying data is unavailable."


def report_ready(url: str) -> str:
    return (
        "Your compensation report is ready. "
        f"[Download the PDF]({url}) — it covers headcount, increments, "
        "variable spend, corrections, equity participation and a data-quality "
        "section, scoped to the population you're allowed to see."
    )


def report_unavailable() -> str:
    return (
        "I couldn't build a report for you — your role doesn't map to a "
        "population to report on, or there's no master data for this cycle."
    )


def blocked_validation() -> str:
    return (
        "I wasn't able to produce a verified answer for that question. The "
        "generated response failed a numeric accuracy check, so it was withheld."
    )
