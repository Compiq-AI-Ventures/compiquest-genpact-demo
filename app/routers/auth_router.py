"""Authentication endpoints (login + me + RBAC sanity checks).

Public self-registration is deliberately absent — see
``app/routers/admin_router.py`` for admin-only user provisioning.

Per-route notes:

* ``/auth/login`` is rate-limited per IP.
* Domain exceptions raised by the service flow through the global
  handler in ``app.core.exceptions`` — no per-route translation here.
* Successful payloads pass through
  :func:`app.utils.response_builder.success_response` for envelope
  consistency.

Tenant context: the route never resolves it itself. ``/auth/me``
calls ``get_active_tenant_id`` (so it can echo it in the response),
and the RBAC test endpoints rely on ``require_tenant_roles`` /
``require_platform_roles`` to enforce + surface it.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization import load_role_profile
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.core.roles import RoleCode
from app.dependencies.auth_dependency import get_current_user
from app.dependencies.db_dependency import get_db
from app.dependencies.role_dependency import (
    require_platform_roles,
    require_tenant_roles,
)
from app.dependencies.tenant_dependency import TenantContext
from app.models.user import User
from app.schemas.auth_schema import RefreshRequest, TokenResponse, UserLoginRequest
from app.schemas.user_schema import CurrentUserResponse
from app.services import audit_log_service, auth_service
from app.utils.response_builder import success_response

router = APIRouter(prefix="/auth", tags=["auth"])

# Resolve at import time so the decorator picks up the env-driven values.
_settings = get_settings()


def _request_context(request: Request) -> dict[str, str | None]:
    """Extract the audit context from the FastAPI Request.

    Centralized so every audit call site uses the same field set and
    no audit ever leaks something it shouldn't (e.g., the full body).
    """
    return {
        "request_id": getattr(request.state, "request_id", None),
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


# Public self-registration was deliberately removed: CompIQCoreBe is an
# enterprise SaaS, so user accounts are created by admins via
# ``POST /admin/users`` (platform users) or ``POST /admin/tenants/{id}/users``
# (tenant users). See ``app/routers/admin_router.py``.


@router.post(
    "/login",
    status_code=status.HTTP_200_OK,
    summary="Log in and receive a JWT access token",
    responses={
        401: {"description": "Invalid credentials"},
        403: {"description": "Requested tenant not accessible"},
        422: {"description": "Validation error"},
        429: {"description": "Rate limit exceeded"},
    },
)
@limiter.limit(_settings.rate_limit_login)
async def login(
    request: Request,
    body: UserLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Authenticate a user and return an access + refresh token pair."""
    tokens = await auth_service.authenticate_user(db, body, **_request_context(request))
    return success_response(
        message="Login successful",
        data=TokenResponse(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_in=tokens.expires_in,
        ),
    )


@router.post(
    "/refresh",
    status_code=status.HTTP_200_OK,
    summary="Exchange a refresh token for a new access + refresh pair",
    responses={
        401: {"description": "Refresh token invalid, expired, or already used"},
        422: {"description": "Validation error"},
        429: {"description": "Rate limit exceeded"},
    },
)
@limiter.limit(_settings.rate_limit_login)
async def refresh(
    request: Request,
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Rotate the caller's refresh token and mint a new access token.

    The presented refresh token is single-use: success revokes it and
    returns a new pair; replay returns 401.
    """
    tokens = await auth_service.refresh_access_token(
        db, body.refresh_token, **_request_context(request)
    )
    return success_response(
        message="Token refreshed",
        data=TokenResponse(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_in=tokens.expires_in,
        ),
    )


@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
    summary="Revoke the current access token",
    responses={
        401: {"description": "Missing or invalid Bearer token"},
    },
)
async def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Revoke the access token used on this request.

    Adds the token's ``jti`` to the deny-list with a TTL equal to the
    token's remaining lifetime. Subsequent requests carrying the same
    token are rejected by ``get_current_user``.

    Idempotent: hitting logout twice with the same token writes the
    same entry the second time and still returns 200. Refresh tokens
    are not touched here — clients that want to invalidate everything
    should logout (revoke access) AND drop their refresh token; we
    can't revoke refresh tokens we never see.
    """
    claims: dict[str, Any] = getattr(request.state, "jwt_claims", {}) or {}
    revoked = await auth_service.revoke_token_claims(claims)

    await audit_log_service.log_action(
        db,
        actor_user_id=current_user.id,
        action="LOGOUT",
        resource_type="user",
        resource_id=str(current_user.id),
        metadata={
            "revoked": revoked,
            # Helpful for operators investigating "why is this token still
            # working?" — the jti lets them find the deny-list entry.
            "jti": claims.get("jti"),
        },
        **_request_context(request),
    )

    return success_response(message="Logged out", data={"revoked": revoked})


@router.get(
    "/me",
    status_code=status.HTTP_200_OK,
    summary="Get the authenticated user's profile",
    responses={
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "User account is inactive"},
    },
)
async def me(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return the profile of the user identified by the Bearer JWT.

    Single-tenant model: the response carries one ``tenant`` object
    (or ``null`` for platform users) and a single flat ``roles`` list.
    """
    profile = await load_role_profile(db, current_user)

    await audit_log_service.log_action(
        db,
        actor_user_id=current_user.id,
        action="CURRENT_USER_VIEWED",
        tenant_id=current_user.tenant_id,
        resource_type="user",
        resource_id=str(current_user.id),
        **_request_context(request),
    )

    return success_response(
        message="Current user profile",
        data=CurrentUserResponse.build(current_user, profile=profile),
    )


# ---------------------------------------------------------------------------
# RBAC test endpoints (kept on the legacy non-enveloped shape on purpose)
# ---------------------------------------------------------------------------
# These exist only to exercise the role-based access dependencies
# end-to-end. They'll be removed (or replaced with real endpoints)
# once business routes start landing.

_RBAC_TEST_RESPONSES = {
    400: {"description": "Tenant context required"},
    401: {"description": "Missing or invalid Bearer token"},
    403: {"description": "Caller's role is not permitted on this endpoint"},
}


@router.get(
    "/platform-admin-test",
    summary="RBAC sanity check — allowed for SUPER_ADMIN or PLATFORM_ADMIN",
    responses=_RBAC_TEST_RESPONSES,
)
async def platform_admin_test(
    current_user: User = Depends(
        require_platform_roles([RoleCode.SUPER_ADMIN, RoleCode.PLATFORM_ADMIN])
    ),
) -> dict[str, str | list[str]]:
    """Endpoint accessible only by platform-level admins."""
    return {
        "message": "Access granted",
        "endpoint": "platform-admin-test",
        "email": current_user.email,
    }


@router.get(
    "/tenant-admin-test",
    summary="RBAC sanity check — allowed for TENANT_ADMIN inside the active tenant",
    responses=_RBAC_TEST_RESPONSES,
)
async def tenant_admin_test(
    ctx: TenantContext = Depends(require_tenant_roles([RoleCode.TENANT_ADMIN])),
) -> dict[str, str]:
    """Endpoint accessible only by TENANT_ADMIN of the active tenant."""
    return {
        "message": "Access granted",
        "endpoint": "tenant-admin-test",
        "email": ctx.user.email,
        "active_tenant_id": str(ctx.active_tenant_id),
    }


@router.get(
    "/admin-test",
    summary="RBAC sanity check — allowed for HR or C_AND_B inside the active tenant",
    responses=_RBAC_TEST_RESPONSES,
)
async def admin_test(
    ctx: TenantContext = Depends(
        require_tenant_roles([RoleCode.HR, RoleCode.C_AND_B])
    ),
) -> dict[str, str | list[str]]:
    """Endpoint accessible only by HR or C_AND_B inside the active tenant."""
    return {
        "message": "Access granted",
        "endpoint": "admin-test",
        "email": ctx.user.email,
        "active_tenant_id": str(ctx.active_tenant_id),
        "roles_in_tenant": sorted(ctx.role_profile.tenant_roles),
    }


@router.get(
    "/manager-test",
    summary="RBAC sanity check — allowed for MANAGER inside the active tenant",
    responses=_RBAC_TEST_RESPONSES,
)
async def manager_test(
    ctx: TenantContext = Depends(require_tenant_roles([RoleCode.MANAGER])),
) -> dict[str, str | list[str]]:
    """Endpoint accessible only by MANAGER inside the active tenant."""
    return {
        "message": "Access granted",
        "endpoint": "manager-test",
        "email": ctx.user.email,
        "active_tenant_id": str(ctx.active_tenant_id),
        "roles_in_tenant": sorted(ctx.role_profile.tenant_roles),
    }
