"""Authorization dependencies for admin endpoints.

The single export here is :func:`require_admin_for_tenant` — used by
``POST /admin/tenants/{tenant_id}/users``. Authorizes any of:

* ``SUPER_ADMIN`` or ``PLATFORM_ADMIN`` at the platform level
  (override — platform staff can administer any tenant).
* ``TENANT_ADMIN`` inside the target tenant.

Returns an :class:`AdminContext` bundle that the route can pass on to
the service. Anything else gets 403.

For the platform-only ``POST /admin/users`` endpoint, the route uses
the existing :func:`require_platform_roles` directly — no custom dep
needed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization import (
    is_authorized_in_tenant,
    is_authorized_platform,
    load_role_profile,
)
from app.core.roles import RoleCode
from app.dependencies.auth_dependency import get_current_user
from app.dependencies.db_dependency import get_db
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories import tenant_repository
from app.services import audit_log_service


@dataclass(frozen=True)
class AdminContext:
    """Successful-admin-authorization bundle handed to route handlers."""

    user: User
    tenant: Tenant
    via_platform_override: bool
    """True if the caller was authorized via SUPER/PLATFORM_ADMIN rather
    than TENANT_ADMIN of this tenant. Useful for audit metadata."""


async def require_admin_for_tenant(
    tenant_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AdminContext:
    """Allow platform admins or the tenant's own ``TENANT_ADMIN``.

    ``tenant_id`` is consumed from the route's path parameter — declare
    the same name in your route's URL pattern (e.g.
    ``"/admin/tenants/{tenant_id}/users"``) and FastAPI resolves it.
    """
    tenant = await tenant_repository.get_by_id(db, tenant_id)
    if tenant is None:
        # 404 here is honest — the tenant doesn't exist. The membership
        # check below would otherwise leak the same information via 403.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found.",
        )

    profile = await load_role_profile(db, current_user)

    # 1. Platform-admin override. Platform admins (no tenant binding)
    #    can administer any tenant for support purposes.
    if is_authorized_platform(profile, [RoleCode.SUPER_ADMIN, RoleCode.PLATFORM_ADMIN]):
        return AdminContext(user=current_user, tenant=tenant, via_platform_override=True)

    # 2. Tenant admin of this specific tenant. With single-tenant-per-
    #    user, this is true iff the caller's own tenant matches the
    #    path AND they hold TENANT_ADMIN.
    if is_authorized_in_tenant(profile, tenant_id, [RoleCode.TENANT_ADMIN]):
        return AdminContext(user=current_user, tenant=tenant, via_platform_override=False)

    # 3. Audit + reject. Logged with tenant_id so operators can filter.
    # Failure path — independent tx so the row survives the rollback.
    await audit_log_service.log_action_independent(
        actor_user_id=current_user.id,
        action="ACCESS_DENIED",
        tenant_id=tenant_id,
        resource_type="endpoint",
        resource_id=request.url.path,
        request_id=getattr(request.state, "request_id", None),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata={
            "method": request.method,
            "scope": "ADMIN_FOR_TENANT",
            "user_platform_roles": sorted(profile.platform_roles),
            # Single-tenant model: tenant_roles is a flat frozenset of
            # codes the user holds in their own tenant. Only meaningful
            # in the audit if the user actually belongs to the path
            # tenant; otherwise it's their roles in some OTHER tenant.
            "user_tenant_roles": sorted(profile.tenant_roles)
            if profile.tenant_id == tenant_id
            else [],
            "user_tenant_id": (
                str(profile.tenant_id) if profile.tenant_id is not None else None
            ),
        },
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have permission to administer this tenant.",
    )
