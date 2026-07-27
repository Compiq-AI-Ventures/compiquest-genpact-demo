"""Authentication-related orchestration (login + logout + refresh).

User creation lives in :mod:`app.services.admin_user_service` — there
is no public ``register_user`` here because this product doesn't ship
self-service signup; accounts are minted by an admin or by the
bootstrap CLI.

Login resolution (single-tenant-per-user)
-----------------------------------------
A user belongs to exactly one tenant or to no tenant (platform user).
Email is unique *per tenant*, so the same email can exist in many
tenants plus optionally as one platform user. Resolution order:

* If the caller supplied ``tenant_code`` they have explicitly named a
  tenant — resolve straight to that tenant's user and stop. (Without
  this short-circuit, a tenant user whose email collides with a
  platform user's would be unreachable via the API.)
* Otherwise:

  1. Try the platform-user lookup first (``users.tenant_id IS NULL``).
  2. On a miss, fall back to the email's domain (``alice@acme.com`` →
     tenant where ``domain = 'acme.com'``) and look the user up there.

If none of those paths produces an active matching user, every
failure mode collapses into a single ``INVALID_CREDENTIALS`` response
so the API can't be used as an enumeration oracle.

Domain exceptions raised here inherit from :class:`DomainError` so the
global exception handler renders them in the standard error envelope.
Sensitive context (the offending email) is kept on instance attributes
for logging — never in the wire-facing ``message``.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import status
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization import load_role_profile
from app.core.config import get_settings
from app.core.exceptions import DomainError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    verify_password,
)
from app.core.token_denylist import get_denylist
from app.models.user import User
from app.repositories import tenant_repository, user_repository
from app.schemas.auth_schema import UserLoginRequest
from app.services import audit_log_service


@dataclass(frozen=True)
class AuthTokens:
    """Bundle returned by login + refresh — both issue an access +
    refresh pair plus the access-token TTL in seconds (matching the
    OAuth2 ``expires_in`` field)."""

    access_token: str
    refresh_token: str
    expires_in: int


class InvalidCredentialsError(DomainError):
    """Email/password/tenant combination doesn't match any user.

    Same exception for every failure mode (no such user, wrong
    password, inactive user, wrong tenant_code, no domain-resolvable
    tenant) — distinguishing them leaks account state to attackers.
    """

    status_code = status.HTTP_401_UNAUTHORIZED
    error_code = "INVALID_CREDENTIALS"

    def __init__(self, message: str = "Invalid email or password.") -> None:
        super().__init__(message=message)


class InvalidRefreshTokenError(DomainError):
    """The refresh token presented at ``/auth/refresh`` is not usable.

    Same generic message as login: every failure mode (expired,
    revoked, signature mismatch, user gone, account inactive, wrong
    type) collapses into one error so an attacker can't tell which.
    """

    status_code = status.HTTP_401_UNAUTHORIZED
    error_code = "INVALID_REFRESH_TOKEN"

    def __init__(self) -> None:
        super().__init__(message="Refresh token is invalid or has expired.")


# ---------------------------------------------------------------------------
# Internal — login resolution
# ---------------------------------------------------------------------------
def _email_domain(email: str) -> str | None:
    """Return the lowercase domain part of ``email``, or ``None``.

    Pydantic's ``EmailStr`` already validates structure; this is just
    a safe split for routing purposes.
    """
    if "@" not in email:
        return None
    _, _, domain = email.partition("@")
    domain = domain.strip().lower()
    return domain or None


async def _resolve_login_user(
    db: AsyncSession, request: UserLoginRequest
) -> User | None:
    """Find the user this login attempt is targeting, or ``None``.

    Returns ``None`` if no candidate is found — the caller treats that
    the same as "wrong password", so the response stays generic.

    Order:

    * ``tenant_code`` supplied → the caller explicitly named a tenant,
      so resolve straight to that tenant's user. We do NOT fall through
      to the platform-user lookup, otherwise a tenant user with the
      same email as a platform user becomes unreachable.
    * No ``tenant_code`` → platform-user lookup first; on miss, try the
      email-domain → tenant mapping.
    """
    # Caller explicitly named a tenant: targeted lookup only.
    if request.tenant_code is not None:
        tenant = await tenant_repository.get_by_code(db, request.tenant_code)
        if tenant is None:
            return None
        return await user_repository.get_tenant_user_by_email(
            db, tenant.id, request.email
        )

    # 1. Platform-user lookup first (no tenant_code given).
    platform_user = await user_repository.get_platform_user_by_email(
        db, request.email
    )
    if platform_user is not None:
        return platform_user

    # 2. Fall back to email-domain → tenant resolution.
    domain = _email_domain(request.email)
    if domain is None:
        return None
    tenant = await tenant_repository.get_by_domain(db, domain)
    if tenant is None:
        return None
    return await user_repository.get_tenant_user_by_email(
        db, tenant.id, request.email
    )


async def authenticate_user(
    db: AsyncSession,
    request: UserLoginRequest,
    *,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuthTokens:
    """Verify credentials and return a fresh access/refresh token pair.

    Failures of any kind collapse into :class:`InvalidCredentialsError`
    so the response and timing don't leak account state. Every failure
    is audited via the independent transaction so the row survives the
    rollback that the raise will trigger.
    """
    user = await _resolve_login_user(db, request)

    valid = (
        user is not None
        and verify_password(request.password, user.password_hash)
        and user.is_active
    )

    if not valid:
        await audit_log_service.log_action_independent(
            actor_user_id=user.id if user is not None else None,
            action="LOGIN_FAILED",
            tenant_id=user.tenant_id if user is not None else None,
            resource_type="user",
            resource_id=str(user.id) if user is not None else None,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={
                # Email is metadata — stored but not exposed via the
                # response. NEVER store the plaintext password.
                "email_attempted": request.email,
                "tenant_code_attempted": request.tenant_code,
                "user_existed": user is not None,
                "password_matched": (
                    user is not None and verify_password(request.password, user.password_hash)
                ),
                "user_active": user.is_active if user is not None else None,
            },
        )
        raise InvalidCredentialsError()

    # Success — assemble claims. The tenant binding is implicit in
    # user.tenant_id; roles bucket cleanly by that.
    profile = await load_role_profile(db, user)
    settings = get_settings()

    access_token = create_access_token(
        data={
            "sub": str(user.id),
            "email": user.email,
            "tenant_id": str(user.tenant_id) if user.tenant_id is not None else None,
            "roles": sorted(profile.platform_roles | profile.tenant_roles),
        }
    )
    refresh_token = create_refresh_token(user_id=str(user.id))

    await audit_log_service.log_action(
        db,
        actor_user_id=user.id,
        action="LOGIN_SUCCESS",
        tenant_id=user.tenant_id,
        resource_type="user",
        resource_id=str(user.id),
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata={
            "email": user.email,
            "tenant_id": str(user.tenant_id) if user.tenant_id is not None else None,
            "roles": sorted(profile.platform_roles | profile.tenant_roles),
        },
    )
    return AuthTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


async def revoke_token_claims(claims: dict[str, Any]) -> bool:
    """Add the token's ``jti`` to the deny-list. Idempotent.

    The TTL we set on the deny-list entry equals the token's remaining
    lifetime — once the token would have expired anyway, the entry is
    purged automatically and the deny-list doesn't grow without bound.

    Returns True if a revocation entry was written, False if the token
    has no ``jti`` claim or has already expired.
    """
    jti = claims.get("jti")
    exp = claims.get("exp")
    if not isinstance(jti, str) or not isinstance(exp, int):
        return False

    ttl = exp - int(time.time())
    if ttl <= 0:
        return False

    await get_denylist().revoke(jti, ttl)
    return True


async def refresh_access_token(
    db: AsyncSession,
    refresh_token: str,
    *,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuthTokens:
    """Exchange a refresh token for a new access + refresh pair.

    Rotation: the presented refresh token is added to the deny-list
    immediately, and a fresh refresh token is minted alongside the new
    access token. A leaked refresh token works at most once.

    Roles are reloaded from the DB so any change since the last login
    (a role grant or revoke, a tenant suspension) takes effect on the
    next refresh — that's the whole point of having short-lived access
    tokens.

    Audit rows: ``REFRESH_SUCCESS`` (atomic with the UoW),
    ``REFRESH_FAILED`` (independent so it survives the rollback).
    """
    settings = get_settings()

    # 1. Validate the token cryptographically.
    try:
        payload = jwt.decode(
            refresh_token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        await audit_log_service.log_action_independent(
            actor_user_id=None,
            action="REFRESH_FAILED",
            resource_type="refresh_token",
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"reason": "decode_error"},
        )
        raise InvalidRefreshTokenError() from exc

    # 2. Must be a refresh token.
    if payload.get("type") != "refresh":
        await audit_log_service.log_action_independent(
            actor_user_id=None,
            action="REFRESH_FAILED",
            resource_type="refresh_token",
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"reason": "wrong_token_type"},
        )
        raise InvalidRefreshTokenError()

    jti = payload.get("jti")
    sub = payload.get("sub")
    if not isinstance(jti, str) or not isinstance(sub, str):
        raise InvalidRefreshTokenError()

    # 3. Reject already-revoked refresh tokens.
    if await get_denylist().is_revoked(jti):
        await audit_log_service.log_action_independent(
            actor_user_id=None,
            action="REFRESH_FAILED",
            resource_type="refresh_token",
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"reason": "revoked", "jti": jti},
        )
        raise InvalidRefreshTokenError()

    # 4. Resolve the user; re-check active state and (if a tenant user)
    #    that the tenant is still ACTIVE.
    try:
        user_id = uuid.UUID(sub)
    except ValueError as exc:
        raise InvalidRefreshTokenError() from exc

    user = await user_repository.get_user_by_id(db, user_id)
    tenant_inactive = (
        user is not None
        and user.tenant_id is not None
        and (user.tenant is None or user.tenant.status != "ACTIVE")
    )
    if user is None or not user.is_active or tenant_inactive:
        await audit_log_service.log_action_independent(
            actor_user_id=user.id if user is not None else None,
            action="REFRESH_FAILED",
            resource_type="user",
            resource_id=str(user.id) if user is not None else None,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={
                "reason": (
                    "tenant_inactive" if tenant_inactive else "user_missing_or_inactive"
                ),
            },
        )
        raise InvalidRefreshTokenError()

    # 5. Rotate: revoke the old refresh token, then mint a fresh pair.
    exp = payload.get("exp")
    if isinstance(exp, int):
        ttl = max(1, exp - int(time.time()))
        await get_denylist().revoke(jti, ttl)

    profile = await load_role_profile(db, user)
    new_access = create_access_token(
        data={
            "sub": str(user.id),
            "email": user.email,
            "tenant_id": str(user.tenant_id) if user.tenant_id is not None else None,
            "roles": sorted(profile.platform_roles | profile.tenant_roles),
        }
    )
    new_refresh = create_refresh_token(user_id=str(user.id))

    await audit_log_service.log_action(
        db,
        actor_user_id=user.id,
        action="REFRESH_SUCCESS",
        tenant_id=user.tenant_id,
        resource_type="user",
        resource_id=str(user.id),
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata={"rotated_jti": jti},
    )

    return AuthTokens(
        access_token=new_access,
        refresh_token=new_refresh,
        expires_in=settings.access_token_expire_minutes * 60,
    )
