"""Admin-driven user provisioning.

Replaces the old public ``/auth/register`` flow. Two entry points:

* :func:`create_platform_user` — creates a user with one or more
  PLATFORM-scope role grants. Caller must already be authorized as
  SUPER_ADMIN or PLATFORM_ADMIN (gated at the route layer).
* :func:`create_tenant_user` — creates a user inside a specific
  tenant with one or more TENANT-scope role grants. Caller must
  already be authorized as TENANT_ADMIN of that tenant or as a
  platform admin (gated at the route layer).

Both functions enforce that the requested role codes match the scope
they're being granted in. They emit a ``USER_CREATED`` audit row
(the old ``USER_REGISTERED`` action is retired with the public
endpoint).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DomainError
from app.core.roles import RoleScope
from app.core.security import hash_password
from app.models.role import Role
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user_role import UserRole
from app.repositories import role_repository, user_repository
from app.schemas.admin_user_schema import (
    AdminCreatePlatformUserRequest,
    AdminCreateTenantUserRequest,
)
from app.services import audit_log_service


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class EmailAlreadyExistsError(DomainError):
    """The email is already registered under another user.

    The wire ``message`` is deliberately generic so an attacker can't
    enumerate registered emails by polling the admin endpoints. The
    actual email is on ``self.email`` for the audit log only.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "EMAIL_ALREADY_EXISTS"

    def __init__(self, email: str) -> None:
        super().__init__(
            message=(
                "Unable to create the user. Please verify your input or "
                "contact support if this persists."
            )
        )
        self.email = email


class InvalidRoleCodeError(DomainError):
    """A role code passed to a user-creation request doesn't exist (or is inactive)."""

    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "INVALID_ROLE_CODE"

    def __init__(self, code: str) -> None:
        super().__init__(message=f"Unknown role code {code!r}.")
        self.code = code


class RoleScopeMismatchError(DomainError):
    """A requested role code's scope didn't match the endpoint."""

    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "ROLE_SCOPE_MISMATCH"

    def __init__(self, code: str, expected_scope: str, actual_scope: str) -> None:
        super().__init__(
            message=(
                f"Role {code!r} has scope {actual_scope!r}, but this endpoint "
                f"only accepts {expected_scope!r}-scope roles."
            ),
            details={"role_code": code, "expected": expected_scope, "actual": actual_scope},
        )
        self.code = code


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
async def _resolve_roles(
    db: AsyncSession, codes: Iterable[str], expected_scope: RoleScope
) -> list[Role]:
    """Look every role code up; reject unknown / inactive / wrong-scope ones."""
    resolved: list[Role] = []
    for code in codes:
        role = await role_repository.get_by_code(db, code)
        if role is None or not role.is_active:
            raise InvalidRoleCodeError(code)
        if role.scope != expected_scope.value:
            raise RoleScopeMismatchError(
                code=code, expected_scope=expected_scope.value, actual_scope=role.scope
            )
        resolved.append(role)
    return resolved


def _build_user(
    *,
    tenant_id: uuid.UUID | None,
    email: str,
    password: str,
    first_name: str,
    last_name: str | None,
) -> User:
    return User(
        tenant_id=tenant_id,
        email=email,
        password_hash=hash_password(password),
        first_name=first_name,
        last_name=last_name,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def create_platform_user(
    db: AsyncSession,
    request: AdminCreatePlatformUserRequest,
    *,
    actor_user_id: uuid.UUID | None,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> User:
    """Provision a platform user (no tenant binding).

    The new user has ``tenant_id IS NULL`` and may only hold
    PLATFORM-scope role grants. Email uniqueness is enforced at the
    platform tier (``UNIQUE NULLS NOT DISTINCT`` on ``users``).
    """
    roles = await _resolve_roles(db, request.role_codes, RoleScope.PLATFORM)

    if await user_repository.email_exists_for_tenant(db, None, request.email):
        raise EmailAlreadyExistsError(request.email)

    user = _build_user(
        tenant_id=None,
        email=request.email,
        password=request.password,
        first_name=request.first_name,
        last_name=request.last_name,
    )

    db.add(user)
    try:
        await db.flush()
        for role in roles:
            db.add(UserRole(user_id=user.id, role_id=role.id))
        await db.flush()
    except IntegrityError as exc:
        # Rollback so the request's Unit of Work doesn't try to commit
        # half-done state. The route handler will see the raised
        # exception and ``get_db`` will skip its own commit.
        await db.rollback()
        raise EmailAlreadyExistsError(request.email) from exc

    # Eagerly populate user.roles before returning. The relationship
    # is ``lazy="selectin"``, but if the route accesses it after this
    # function returns, the access happens outside an awaitable and
    # async SQLAlchemy raises ``MissingGreenlet``. Refreshing here
    # makes the role list available without that hazard.
    await db.refresh(user, ["roles"])

    # Atomic with the user creation — if the route raises after this,
    # the UoW rolls everything back together.
    await audit_log_service.log_action(
        db,
        actor_user_id=actor_user_id,
        action="USER_CREATED",
        resource_type="user",
        resource_id=str(user.id),
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata={
            "email": user.email,
            "scope": "PLATFORM",
            "role_codes": [r.code for r in roles],
        },
    )
    return user


async def create_tenant_user(
    db: AsyncSession,
    tenant: Tenant,
    request: AdminCreateTenantUserRequest,
    *,
    actor_user_id: uuid.UUID | None,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> User:
    """Provision a tenant user (``user.tenant_id = tenant.id``).

    The new user may only hold TENANT-scope role grants. Email
    uniqueness is enforced *within* the tenant — two different
    tenants can both have ``alice@hr.com``.
    """
    roles = await _resolve_roles(db, request.role_codes, RoleScope.TENANT)

    if await user_repository.email_exists_for_tenant(db, tenant.id, request.email):
        raise EmailAlreadyExistsError(request.email)

    user = _build_user(
        tenant_id=tenant.id,
        email=request.email,
        password=request.password,
        first_name=request.first_name,
        last_name=request.last_name,
    )

    db.add(user)
    try:
        await db.flush()
        for role in roles:
            db.add(UserRole(user_id=user.id, role_id=role.id))
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise EmailAlreadyExistsError(request.email) from exc

    # Eagerly populate user.roles before returning — see the matching
    # comment in ``create_platform_user`` for why.
    await db.refresh(user, ["roles"])

    await audit_log_service.log_action(
        db,
        actor_user_id=actor_user_id,
        action="USER_CREATED",
        tenant_id=tenant.id,
        resource_type="user",
        resource_id=str(user.id),
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata={
            "email": user.email,
            "scope": "TENANT",
            "tenant_code": tenant.code,
            "role_codes": [r.code for r in roles],
        },
    )
    return user
