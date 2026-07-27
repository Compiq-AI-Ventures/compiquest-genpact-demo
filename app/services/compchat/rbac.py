"""Layer 2 — three-state, field-level RBAC.

``can_access`` is called before any data retrieval (Guardrail 5). It
returns ALLOW / DENY / PARTIAL_ACCESS together with the field allowlist
the tools must filter to. PARTIAL_ACCESS lets a requester receive
salary while bonus/LTI is withheld in the same response — the
framework's headline advance over binary scope filtering.

Two gates compose:

* **Row gate** — may this requester see *this* subject at all? Broad
  roles (HR, C&B, CHRO, CXO, tenant admin) see everyone in the tenant;
  managers see only their reporting chain (reuses the existing
  ``assert_subject_in_reporting_chain``); everyone can see themselves.
* **Field gate** — *which* fields, by role. Hard-blocked fields (SSN,
  bank, personal email) are denied for everyone, always — they are not
  in the Tessot schema today, but the block is encoded so it holds the
  instant such columns are added.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import RoleCode
from app.dependencies.tenant_dependency import TenantContext
from app.services import jvre_workspace_service

from .schemas import AccessDecision, AccessState

# --- Field universe --------------------------------------------------------
COMP_FIELDS: frozenset[str] = frozenset(
    {
        "base_salary",
        "bonus_actual",
        "bonus_target_pct",
        "total_cash",
        "lti_value",
        "compa_ratio",
        "benchmark_p50",
    }
)
PERF_FIELDS: frozenset[str] = frozenset({"rating", "promotion_flag"})
ALL_FIELDS: frozenset[str] = COMP_FIELDS | PERF_FIELDS

# Never reach the LLM regardless of role (Guardrail 9). Encoded ahead of
# the columns existing so the block is already in force when they land.
HARD_BLOCKED: frozenset[str] = frozenset({"ssn", "bank_details", "personal_email"})

# Roles that see every employee in the tenant without a reporting-chain
# check (HR / C&B / exec tier).
_TENANT_WIDE_ROLES: frozenset[str] = frozenset(
    {
        RoleCode.TENANT_ADMIN,
        RoleCode.CXO,
        RoleCode.CHRO,
        RoleCode.HR,
        RoleCode.C_AND_B,
        RoleCode.CFO,
    }
)
_MANAGER_ROLES: frozenset[str] = frozenset(
    {RoleCode.MANAGER, RoleCode.MANAGER_OF_MANAGERS}
)


def is_tenant_wide(ctx: TenantContext) -> bool:
    """True when the caller's role sees every employee in the tenant
    (HR / C&B / exec tier) — used to scope the batch report."""
    return bool(ctx.role_profile.tenant_roles & _TENANT_WIDE_ROLES)


def is_manager(ctx: TenantContext) -> bool:
    """True when the caller holds a manager role (scopes the report to
    their reporting subtree)."""
    return bool(ctx.role_profile.tenant_roles & _MANAGER_ROLES)


async def _subject_in_chain(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    requester_id: uuid.UUID,
    subject_user_id: uuid.UUID,
) -> bool:
    try:
        await jvre_workspace_service.assert_subject_in_reporting_chain(
            db, tenant_id, requester_id, subject_user_id
        )
        return True
    except Exception:
        return False


async def can_access(
    db: AsyncSession,
    ctx: TenantContext,
    subject_user_id: uuid.UUID,
) -> AccessDecision:
    """Decide access for ``ctx.user`` → ``subject_user_id``.

    Pure on the field gate; touches the DB only for the manager row
    gate. Returns the field allowlist tools/context-builder filter to.
    """
    roles = ctx.role_profile.tenant_roles
    is_self = ctx.user.id == subject_user_id

    # --- Field gate (by most-privileged role) ---
    if is_self or roles & _TENANT_WIDE_ROLES:
        allowed = ALL_FIELDS
        partial = False
    elif RoleCode.HRBP in roles:
        # HRBP advises but does not see equity grants — demonstrates
        # field-level withholding (PARTIAL_ACCESS).
        allowed = ALL_FIELDS - {"lti_value"}
        partial = True
    elif roles & _MANAGER_ROLES:
        allowed = ALL_FIELDS
        partial = False
    else:
        return AccessDecision(
            state=AccessState.DENY,
            reason="Role does not grant access to compensation data.",
        )

    # --- Row gate ---
    needs_chain = not is_self and not (roles & _TENANT_WIDE_ROLES)
    if needs_chain and not await _subject_in_chain(
        db, ctx.active_tenant_id, ctx.user.id, subject_user_id
    ):
        return AccessDecision(
            state=AccessState.DENY,
            reason="Subject is not in your reporting chain.",
        )

    state = AccessState.PARTIAL_ACCESS if partial else AccessState.ALLOW
    return AccessDecision(
        state=state,
        allowed_fields=frozenset(allowed),
        denied_fields=(ALL_FIELDS - allowed) | HARD_BLOCKED,
        reason="" if not partial else "Equity (LTI) withheld for this role.",
    )
