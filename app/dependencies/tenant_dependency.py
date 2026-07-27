"""Tenant-context dependencies.

Single-tenant-per-user model
----------------------------
A user belongs to exactly one tenant (``user.tenant_id``) or to no
tenant at all (platform user, ``user.tenant_id IS NULL``). There is
therefore no "which tenant am I acting in?" ambiguity to resolve and
no ``X-Tenant-ID`` header to consult — the answer is always
``user.tenant_id``.

Two dependencies live here:

* :func:`get_active_tenant_id` — returns ``user.tenant_id`` (or
  ``None`` for platform users). Tenant-scoped routes that require a
  tenant turn ``None`` into a 400 via
  :class:`TenantContextRequiredError`.
* :func:`get_tenant_context` — bundles the user, their tenant, and
  their :class:`RoleProfile` for handlers that want all three.

Tenant status enforcement
-------------------------
A user whose tenant is SUSPENDED or DISABLED cannot act on tenant-
scoped endpoints — :class:`TenantInactiveError` (403) is raised.
Platform admins reach SUSPENDED/DISABLED tenants via the admin-for-
tenant dependency, which intentionally bypasses this check.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization import RoleProfile, load_role_profile
from app.core.exceptions import DomainError
from app.dependencies.auth_dependency import get_current_user
from app.dependencies.db_dependency import get_db
from app.models.user import User


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class TenantContextRequiredError(DomainError):
    """Raised when an endpoint needs a tenant context but the caller has none.

    Almost always: a platform user (``tenant_id IS NULL``) called a
    tenant-scoped endpoint that doesn't have a platform-admin override.
    Platform admins use the admin endpoints (with the tenant id in the
    URL path) for cross-tenant work.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "TENANT_CONTEXT_REQUIRED"

    def __init__(self) -> None:
        super().__init__(
            message=(
                "This endpoint requires a tenant context. Platform users "
                "should use the admin endpoints with an explicit tenant id."
            )
        )


class TenantInactiveError(DomainError):
    """Raised when the caller's tenant is SUSPENDED or DISABLED.

    Tenant users cannot operate inside a non-ACTIVE tenant. Platform
    admins reach those tenants via the admin-for-tenant dependency,
    which intentionally bypasses this check so support staff can
    investigate.
    """

    status_code = status.HTTP_403_FORBIDDEN
    error_code = "TENANT_INACTIVE"

    def __init__(self, tenant_status: str) -> None:
        super().__init__(
            message=(
                f"This tenant is {tenant_status!r} and cannot be used. "
                "Contact your administrator if this is unexpected."
            ),
            details={"tenant_status": tenant_status},
        )


# ---------------------------------------------------------------------------
# Tenant context bundle
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TenantContext:
    """Everything a tenant-scoped handler needs about the caller."""

    user: User
    active_tenant_id: uuid.UUID
    role_profile: RoleProfile


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
async def get_active_tenant_id(
    current_user: User = Depends(get_current_user),
) -> uuid.UUID | None:
    """Return the caller's tenant id, or ``None`` for platform users.

    Enforces tenant status: if the caller's tenant is SUSPENDED or
    DISABLED, raises :class:`TenantInactiveError`. Platform users
    (no tenant binding) return ``None`` here — callers that require a
    tenant raise :class:`TenantContextRequiredError`.
    """
    if current_user.tenant_id is None:
        return None

    tenant = current_user.tenant
    if tenant is None:
        # Defensive: the FK is enforced, but if a relationship load
        # somehow failed, treat as no context rather than crashing.
        return None

    if tenant.status != "ACTIVE":
        raise TenantInactiveError(tenant.status)

    return current_user.tenant_id


async def get_tenant_context(
    current_user: User = Depends(get_current_user),
    active_tenant_id: uuid.UUID | None = Depends(get_active_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> TenantContext:
    """Bundle current_user + tenant id + RoleProfile.

    Raises :class:`TenantContextRequiredError` (400) for platform users
    who hit a tenant-scoped endpoint — they should be using the admin
    routes with an explicit tenant id in the path.
    """
    if active_tenant_id is None:
        raise TenantContextRequiredError()

    profile = await load_role_profile(db, current_user)
    return TenantContext(
        user=current_user,
        active_tenant_id=active_tenant_id,
        role_profile=profile,
    )
