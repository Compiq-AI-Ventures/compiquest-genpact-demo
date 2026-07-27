"""Role-based access control as FastAPI dependency factories.

Two factories live here, mirroring the two role scopes:

* :func:`require_platform_roles` — the caller must be a platform user
  (``users.tenant_id IS NULL``) holding one of the given roles.
* :func:`require_tenant_roles` — the caller must be a tenant user
  (``users.tenant_id IS NOT NULL``) holding one of the given roles,
  acting inside their own tenant (single-tenant-per-user). The active
  tenant is read from ``users.tenant_id`` — there is no cross-tenant
  switching.

Both factories accept either :class:`RoleCode` members (preferred —
gives IDE rename safety) or plain strings; arguments are validated
against :data:`ALL_ROLES` at module-load time, so a typo blows up at
startup, not on the first request.

The single point where an authorization decision is made is in
:mod:`app.core.authorization`; this module is just the FastAPI
glue around it. Migrating to a policy engine (Casbin / OPA) means
swapping the bodies of ``is_authorized_*`` — every route declaration
and every dependency below stays the same.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization import (
    is_authorized_in_tenant,
    is_authorized_platform,
    load_role_profile,
)
from app.core.roles import ALL_ROLES
from app.dependencies.auth_dependency import get_current_user
from app.dependencies.db_dependency import get_db
from app.dependencies.tenant_dependency import (
    TenantContext,
    TenantContextRequiredError,
    get_active_tenant_id,
)
from app.models.user import User
from app.services import audit_log_service


def _normalize(allowed_roles: Sequence[str]) -> frozenset[str]:
    """Validate + normalize a role allow-list at factory-call time."""
    if not allowed_roles:
        # An empty allow-list silently denies everyone — almost never
        # what you meant. Better to fail loudly at startup.
        raise ValueError("allowed_roles needs at least one role.")

    normalized = frozenset(str(r) for r in allowed_roles)
    unknown = normalized - ALL_ROLES
    if unknown:
        raise ValueError(
            f"unknown role(s): {sorted(unknown)}. "
            f"Valid roles: {sorted(ALL_ROLES)}"
        )
    return normalized


def require_platform_roles(allowed_roles: Sequence[str]) -> Callable[..., User]:
    """Dependency: caller must hold one of ``allowed_roles`` at the platform level.

    Returns the authenticated :class:`User` on success. Raises 403 on
    mismatch and writes an ``ACCESS_DENIED`` audit row before doing so.
    """
    normalized = _normalize(allowed_roles)

    async def _checker(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        profile = await load_role_profile(db, current_user)
        if not is_authorized_platform(profile, normalized):
            # Failure path — UoW will roll back when we raise. Use the
            # independent audit so the row survives the rollback.
            await audit_log_service.log_action_independent(
                actor_user_id=current_user.id,
                action="ACCESS_DENIED",
                resource_type="endpoint",
                resource_id=request.url.path,
                request_id=getattr(request.state, "request_id", None),
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                metadata={
                    "method": request.method,
                    "scope": "PLATFORM",
                    "user_platform_roles": sorted(profile.platform_roles),
                    "required_roles": sorted(normalized),
                },
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this resource.",
            )
        return current_user

    return _checker


def require_tenant_roles(allowed_roles: Sequence[str]) -> Callable[..., TenantContext]:
    """Dependency: caller must be a tenant user holding one of ``allowed_roles``.

    Returns a :class:`TenantContext` bundle (user + tenant id + profile)
    on success. Failure modes:

    * Platform user (no tenant binding) hitting a tenant route → 400
      ``TENANT_CONTEXT_REQUIRED``.
    * Tenant user whose tenant is SUSPENDED/DISABLED → 403
      ``TENANT_INACTIVE`` (raised inside ``get_active_tenant_id``).
    * Tenant user without one of the required roles → 403, with an
      ``ACCESS_DENIED`` audit row.
    """
    normalized = _normalize(allowed_roles)

    async def _checker(
        request: Request,
        current_user: User = Depends(get_current_user),
        active_tenant_id=Depends(get_active_tenant_id),
        db: AsyncSession = Depends(get_db),
    ) -> TenantContext:
        if active_tenant_id is None:
            raise TenantContextRequiredError()

        profile = await load_role_profile(db, current_user)
        if not is_authorized_in_tenant(profile, active_tenant_id, normalized):
            # Failure path — audit on its own committed tx so the row
            # outlives the rollback that the 403 will trigger.
            await audit_log_service.log_action_independent(
                actor_user_id=current_user.id,
                action="ACCESS_DENIED",
                tenant_id=active_tenant_id,
                resource_type="endpoint",
                resource_id=request.url.path,
                request_id=getattr(request.state, "request_id", None),
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                metadata={
                    "method": request.method,
                    "scope": "TENANT",
                    "user_tenant_roles": sorted(profile.tenant_roles),
                    "required_roles": sorted(normalized),
                },
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this resource.",
            )

        return TenantContext(
            user=current_user,
            active_tenant_id=active_tenant_id,
            role_profile=profile,
        )

    return _checker
