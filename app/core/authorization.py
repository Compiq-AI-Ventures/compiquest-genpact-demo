"""Authorization primitives — the seam between role storage and policy.

Authorization decisions in this codebase pass through this module.
Two seams matter:

* :func:`load_role_profile` — answers "what roles does this user hold?"
* :func:`is_authorized_platform` / :func:`is_authorized_in_tenant` —
  answer "is this user allowed?" (platform-wide vs. inside a tenant).

Routers and services never call these directly; they go through the
dependencies in :mod:`app.dependencies.role_dependency`. The point is
encapsulation: when the role model changes, only this file changes.

Single-tenant-per-user model
----------------------------
A user belongs to exactly one tenant (or to no tenant — a platform
user). The tenant a role grant applies to is therefore implicit: it's
``user.tenant_id``. Roles bucket cleanly by the user's tenant binding:

* Platform user (``user.tenant_id IS NULL``) → all grants are
  PLATFORM-scope; populated in :attr:`RoleProfile.platform_roles`.
* Tenant user (``user.tenant_id IS NOT NULL``) → all grants are
  TENANT-scope, all targeting the user's single tenant; populated in
  :attr:`RoleProfile.tenant_roles` together with the tenant id.

Scope-vs-binding consistency is enforced at write time by
``admin_user_service``; this loader trusts that invariant.

Next step (permissions / policy engine)
---------------------------------------
Add a ``permissions`` table (or a Casbin/OPA enforcer). Replace the
``is_authorized_*`` bodies with engine calls. Routes still declare
``require_platform_roles([...])`` / ``require_tenant_roles([...])``;
the engine translates roles to permissions internally.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.role import Role
from app.models.user import User
from app.models.user_role import UserRole


@dataclass(frozen=True)
class RoleProfile:
    """A user's authorization profile.

    Exactly one of :attr:`platform_roles` or :attr:`tenant_roles` is
    populated for any real user; the unused side is empty. The
    :attr:`tenant_id` field carries the user's tenant binding (mirrors
    ``user.tenant_id``) so callers don't have to pass it in alongside.

    * Platform user → ``tenant_id is None``, ``platform_roles`` non-empty.
    * Tenant user   → ``tenant_id`` set,    ``tenant_roles``   non-empty.
    """

    tenant_id: uuid.UUID | None = None
    platform_roles: frozenset[str] = field(default_factory=frozenset)
    tenant_roles: frozenset[str] = field(default_factory=frozenset)


async def load_role_profile(db: AsyncSession, user: User) -> RoleProfile:
    """Build the user's :class:`RoleProfile` from ``user_roles + roles``.

    One SQL query; bucket the codes into platform vs. tenant by the
    user's tenant binding. Returns an empty profile if the user holds
    no role grants.
    """
    stmt = (
        select(Role.code)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user.id)
    )
    codes = frozenset((await db.execute(stmt)).scalars().all())

    if user.tenant_id is None:
        return RoleProfile(
            tenant_id=None,
            platform_roles=codes,
            tenant_roles=frozenset(),
        )
    return RoleProfile(
        tenant_id=user.tenant_id,
        platform_roles=frozenset(),
        tenant_roles=codes,
    )


def is_authorized_platform(
    profile: RoleProfile, allowed_roles: Iterable[str]
) -> bool:
    """True iff the user holds at least one role in ``allowed_roles``
    at the platform level."""
    return bool(profile.platform_roles & frozenset(allowed_roles))


def is_authorized_in_tenant(
    profile: RoleProfile,
    tenant_id: uuid.UUID,
    allowed_roles: Iterable[str],
) -> bool:
    """True iff the user is a member of ``tenant_id`` AND holds at
    least one of ``allowed_roles`` inside it.

    Returns False for platform users — they reach tenant-scoped
    endpoints through the platform-admin override (see
    :func:`app.dependencies.admin_dependency.require_admin_for_tenant`),
    not through this check.
    """
    if profile.tenant_id != tenant_id:
        return False
    return bool(profile.tenant_roles & frozenset(allowed_roles))


# ---------------------------------------------------------------------------
# Backwards-compat
# ---------------------------------------------------------------------------
def get_subject_roles(user: User) -> frozenset[str]:
    """Flat set of every role code the user holds.

    Retained for diagnostic / display purposes. Authorization decisions
    should use ``is_authorized_*``.
    """
    return frozenset(r.code for r in user.roles)
