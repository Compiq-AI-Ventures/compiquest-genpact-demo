"""Layer 4 — intent classifier (rules-first, SLM-assisted).

Produces a :class:`Classification`. The model never selects tools; it
only fills a small, schema-constrained JSON object. The pipeline maps
the resulting intent to tools deterministically via
``tools.INTENT_TOOLS``.

Reliability stack:

1. **Rules-first fast path** — unambiguous keyword hits classify with
   zero model cost and 100% determinism.
2. **Schema-constrained SLM** — for everything else, Ollama is forced
   (via ``format`` grammar) to emit exactly the :class:`Classification`
   shape with an enum-bounded ``intent``.
3. **Repair / fallback** — parse into Pydantic; on any failure drop back
   to the rules result, and if that is UNKNOWN return UNKNOWN (a
   structured "cannot answer", never a guess).

A malformed model output therefore never reaches tool execution.
"""

from __future__ import annotations

import logging

from . import slm
from .schemas import AnalyticsScope, Classification, IntentType

logger = logging.getLogger(__name__)

# Keyword → intent for the fast path. Order matters: the first intent
# whose keywords appear wins, so more specific intents come first.
_RULES: tuple[tuple[IntentType, tuple[str, ...]], ...] = (
    (IntentType.REPORT_REQUEST, ("generate report", "generate a report", "create report", "create a report", "compensation report", "download report", "pdf report", "full report", "cycle report", "build a report")),
    (IntentType.COMPARISON_QUERY, ("compare", "versus", " vs ", "side by side", "difference between")),
    (IntentType.ANALYTICS_QUERY, ("average", "median", "across the team", "job family", "distribution", "aggregate", "spread")),
    (IntentType.PROMOTION_QUERY, ("promotion", "promoted", "level history", "last promoted", "eligible for promotion")),
    (IntentType.TEAM_QUERY, ("direct reports", "my team", "span of control", "who reports", "org structure", "reportees")),
    (IntentType.PERFORMANCE_QUERY, ("rating", "performance", "payout", "appraisal", "multiplier")),
    (IntentType.COMPENSATION_QUERY, (
        "salary", "bonus", "compa", "compensation", "pay", "lti", "equity", "band", "total cash",
        # Market position / competitiveness
        "market", "benchmark", "competitive", "midpoint", "above market", "below market", "worth",
        # Retention / flight risk
        "retention", "retain", "flight risk", "attrition", "turnover", "risk of leaving", "leave",
        "critical", "criticality", "replace", "replacement", "cost to replace",
        # The recommendation / increase itself
        "increase", "raise", "hike", "adjustment", "recommended", "recommendation", "justified",
        "justify", "justification", "why this", "how much",
        # Budget affordability
        "budget", "headroom", "room", "afford", "affordable", "pool",
        # Equity / vesting / tenure / package
        "vest", "vesting", "unvested", "tenure", "package", "reward", "increment",
    )),
)

# JSON Schema handed to Ollama's grammar-constrained ``format``.
_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": [i.value for i in IntentType]},
        "secondary_name": {"type": ["string", "null"]},
        "fiscal_year": {"type": ["integer", "null"]},
        "analytics_scope": {"type": ["string", "null"], "enum": ["TEAM", "JOB_FAMILY", None]},
    },
    "required": ["intent"],
}

_FEWSHOT = """\
Classify the manager's question into exactly one intent. Intents:
- COMPENSATION_QUERY: base salary, bonus, LTI/equity, market position/competitiveness, retention or flight risk, criticality/replacement cost, the recommended increase and its justification, budget room/affordability, vesting, and tenure. This is the default for any question about ONE employee's pay, market standing, retention, or the recommendation.
- PERFORMANCE_QUERY: rating, payout percentage, performance multiplier.
- PROMOTION_QUERY: level history, months since last promotion, eligibility.
- TEAM_QUERY: direct reports, org structure, span of control.
- COMPARISON_QUERY: two employees compared side by side.
- ANALYTICS_QUERY: aggregate statistics across a team or job family.
- UNKNOWN: anything outside the above (small talk, unrelated, unanswerable).

If a second employee is named (for a comparison or a topic switch), put their
name in secondary_name. For ANALYTICS_QUERY set analytics_scope to TEAM or
JOB_FAMILY. Only set fiscal_year if the question explicitly states a year.

Examples:
Q: "Why is the bonus below target this year?" -> {"intent":"COMPENSATION_QUERY","secondary_name":null,"fiscal_year":null,"analytics_scope":null}
Q: "How does she compare to Rahul on pay?" -> {"intent":"COMPARISON_QUERY","secondary_name":"Rahul","fiscal_year":null,"analytics_scope":null}
Q: "What's the average salary across the engineering job family?" -> {"intent":"ANALYTICS_QUERY","secondary_name":null,"fiscal_year":null,"analytics_scope":"JOB_FAMILY"}
Q: "When was he last promoted?" -> {"intent":"PROMOTION_QUERY","secondary_name":null,"fiscal_year":null,"analytics_scope":null}
Q: "Who are my direct reports?" -> {"intent":"TEAM_QUERY","secondary_name":null,"fiscal_year":null,"analytics_scope":null}
Q: "What is the retention risk for this person?" -> {"intent":"COMPENSATION_QUERY","secondary_name":null,"fiscal_year":null,"analytics_scope":null}
Q: "Is the recommended increase justified?" -> {"intent":"COMPENSATION_QUERY","secondary_name":null,"fiscal_year":null,"analytics_scope":null}
Q: "Is there budget room to give more?" -> {"intent":"COMPENSATION_QUERY","secondary_name":null,"fiscal_year":null,"analytics_scope":null}
Q: "How does their pay compare to the market rate for the role?" -> {"intent":"COMPENSATION_QUERY","secondary_name":null,"fiscal_year":null,"analytics_scope":null}
"""


def _rules_classify(question: str) -> IntentType:
    q = f" {question.lower()} "
    for intent, keywords in _RULES:
        if any(kw in q for kw in keywords):
            return intent
    return IntentType.UNKNOWN


def fallback_from_history(prior_user_messages: list[str]) -> IntentType:
    """The most recent classifiable intent from earlier user turns.

    Lets a follow-up that carries no intent of its own (a correction,
    "why?", "and the bonus?") inherit the topic of the conversation
    instead of dropping to out-of-scope.
    """
    for text in reversed(prior_user_messages):
        it = _rules_classify(text)
        if it is not IntentType.UNKNOWN:
            return it
    return IntentType.UNKNOWN


async def classify(
    base_url: str, model: str, question: str
) -> Classification:
    """Classify ``question`` into a :class:`Classification`."""
    rules_intent = _rules_classify(question)

    # Fast path: a confident keyword hit with no secondary entity and no
    # analytics grouping needed skips the model entirely.
    if rules_intent in (
        IntentType.COMPENSATION_QUERY,
        IntentType.PERFORMANCE_QUERY,
        IntentType.PROMOTION_QUERY,
        IntentType.TEAM_QUERY,
        IntentType.REPORT_REQUEST,
    ):
        return Classification(intent=rules_intent)

    prompt = f'{_FEWSHOT}\n\nQ: "{question.strip()}" ->'
    try:
        raw = await slm.complete_json(base_url, model, prompt, _CLASSIFY_SCHEMA)
        parsed = Classification.model_validate(raw)
    except Exception:
        logger.warning("compchat.classify SLM path failed; using rules fallback", exc_info=True)
        return Classification(intent=rules_intent)

    # Coherence guard: ANALYTICS without a scope defaults to TEAM;
    # COMPARISON without a secondary name is not a comparison.
    if parsed.intent is IntentType.ANALYTICS_QUERY and parsed.analytics_scope is None:
        parsed = parsed.model_copy(update={"analytics_scope": AnalyticsScope.TEAM})
    if parsed.intent is IntentType.COMPARISON_QUERY and not parsed.secondary_name:
        # No second party named — fall back to the single-subject reading.
        parsed = parsed.model_copy(
            update={"intent": rules_intent if rules_intent is not IntentType.UNKNOWN else IntentType.COMPENSATION_QUERY}
        )
    return parsed
