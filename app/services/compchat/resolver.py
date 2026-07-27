"""Layer 3 — employee resolver.

Two entry points:

* :func:`resolve_injected` — the dashboard has an open profile card, so
  ``subject_user_id`` is already known. Resolution is skipped; we only
  hydrate the name + Tessot id. This is the common path for the
  embedded chat panel.
* :func:`resolve_by_name` — a name appeared in the question (a
  comparison target, or an in-conversation entity switch). We query the
  directory and STOP on ambiguity rather than guess (Guardrail 7).

After resolution only ``user_id`` / ``employee_id`` travel downstream;
the raw name is used for narration only.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

from . import tools
from .schemas import ResolvedSubject, ResolverOutcome

# Cap the candidate list returned on ambiguity so a too-broad name
# ("a") doesn't dump the whole directory into a clarification prompt.
_MAX_CANDIDATES = 5


def _full_name(user: User) -> str:
    last = f" {user.last_name}" if user.last_name else ""
    return f"{user.first_name}{last}".strip()


async def resolve_injected(
    db: AsyncSession, tenant_id: uuid.UUID, subject_user_id: uuid.UUID
) -> ResolverOutcome:
    """Hydrate an already-known subject id into a :class:`ResolvedSubject`."""
    user = await db.get(User, subject_user_id)
    if user is None or user.tenant_id != tenant_id:
        return ResolverOutcome(status="NOT_FOUND")
    emp_id = await tools.resolve_tessot_id(db, tenant_id, subject_user_id)
    if emp_id is None:
        return ResolverOutcome(status="NOT_FOUND")
    return ResolverOutcome(
        status="RESOLVED",
        subject=ResolvedSubject(user_id=user.id, employee_id=emp_id, name=_full_name(user)),
    )


async def resolve_by_name(
    db: AsyncSession, tenant_id: uuid.UUID, name: str
) -> ResolverOutcome:
    """Resolve a free-text name to a unique subject, or STOP."""
    needle = name.strip().lower()
    if not needle:
        return ResolverOutcome(status="NOT_FOUND")

    # Match against "first last" (and bare first name) within the tenant.
    full = func.lower(
        func.concat(User.first_name, " ", func.coalesce(User.last_name, ""))
    )
    rows = await db.execute(
        select(User)
        .where(
            User.tenant_id == tenant_id,
            func.lower(User.first_name).contains(needle) | full.contains(needle),
        )
        .limit(_MAX_CANDIDATES + 1)
    )
    matches = list(rows.scalars().all())

    if not matches:
        return ResolverOutcome(status="NOT_FOUND")

    candidates: list[ResolvedSubject] = []
    for u in matches[:_MAX_CANDIDATES]:
        emp_id = await tools.resolve_tessot_id(db, tenant_id, u.id)
        candidates.append(
            ResolvedSubject(user_id=u.id, employee_id=emp_id or "", name=_full_name(u))
        )

    if len(candidates) == 1 and candidates[0].employee_id:
        return ResolverOutcome(status="RESOLVED", subject=candidates[0])
    return ResolverOutcome(status="AMBIGUOUS", candidates=tuple(candidates))
