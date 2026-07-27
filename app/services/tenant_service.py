"""Tenant lifecycle orchestration.

Three operations:

* :func:`create_tenant_with_admin` — atomic create-tenant + create-the-
  bootstrap-TENANT_ADMIN. Both rows commit together so a partial
  failure can't leave a tenant nobody can administer.
* :func:`list_tenants`             — paginated, optionally filtered.
* :func:`update_tenant`            — name / domain / status. Status
  transitions are gated: ``DISABLED`` is terminal.

Audit rows: ``TENANT_CREATED`` and ``TENANT_UPDATED``. The bootstrap
admin's ``USER_CREATED`` row is also written by this module rather
than by ``admin_user_service``, because the user-creation logic is
inlined here to keep the create flow in a single transaction.
"""

from __future__ import annotations

import uuid

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DomainError
from app.core.security import hash_password
from app.models.tenant import Tenant, TenantStatus
from app.models.user import User
from app.models.user_role import UserRole
from app.repositories import role_repository, tenant_repository
from app.schemas.tenant_schema import TenantCreateRequest, TenantUpdateRequest
from app.services import audit_log_service


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class TenantCodeAlreadyExistsError(DomainError):
    """A tenant with this ``code`` already exists."""

    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "TENANT_CODE_ALREADY_EXISTS"

    def __init__(self, code: str) -> None:
        super().__init__(message=f"Tenant code {code!r} is already in use.")
        self.code = code


class TenantDomainAlreadyExistsError(DomainError):
    """A tenant with this ``domain`` already exists.

    Domain is globally unique because it's the canonical email/SSO
    discovery anchor. Two tenants claiming the same domain would
    create an unresolvable login: ``alice@acme.com`` would be
    ambiguous between them.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "TENANT_DOMAIN_ALREADY_EXISTS"

    def __init__(self, domain: str) -> None:
        super().__init__(
            message=f"Tenant domain {domain!r} is already in use by another tenant."
        )
        self.domain = domain


class TenantStatusTransitionError(DomainError):
    """An illegal status transition was requested."""

    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "TENANT_STATUS_TRANSITION_INVALID"

    def __init__(self, current: str, requested: str) -> None:
        super().__init__(
            message=(
                f"Cannot transition tenant status from {current!r} to "
                f"{requested!r}. Once DISABLED, tenants cannot be reactivated."
            ),
            details={"current_status": current, "requested_status": requested},
        )


# ---------------------------------------------------------------------------
# Status transition rules
# ---------------------------------------------------------------------------
_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    TenantStatus.ACTIVE.value: frozenset(
        {TenantStatus.SUSPENDED.value, TenantStatus.DISABLED.value}
    ),
    TenantStatus.SUSPENDED.value: frozenset(
        {TenantStatus.ACTIVE.value, TenantStatus.DISABLED.value}
    ),
    TenantStatus.DISABLED.value: frozenset(),  # terminal
}


def _is_valid_transition(current: str, requested: str) -> bool:
    if current == requested:
        return True  # idempotent — same-status updates are allowed
    return requested in _VALID_TRANSITIONS.get(current, frozenset())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def create_tenant_with_admin(
    db: AsyncSession,
    request: TenantCreateRequest,
    *,
    actor_user_id: uuid.UUID | None,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[Tenant, User]:
    """Create the tenant and its first ``TENANT_ADMIN`` atomically.

    Both rows commit in the same transaction, so a partial failure
    (e.g., the admin's email collides with an existing user) rolls
    back the tenant too. The caller is responsible for retrying with
    a different code / email.
    """
    # 1. Pre-check tenant code uniqueness so the common conflict is a
    #    clean 400 instead of a database integrity error.
    if await tenant_repository.get_by_code(db, request.code) is not None:
        raise TenantCodeAlreadyExistsError(request.code)

    # 2. Pre-check tenant domain uniqueness — globally unique because
    #    it's the email/SSO discovery anchor.
    if await tenant_repository.get_by_domain(db, request.domain) is not None:
        raise TenantDomainAlreadyExistsError(request.domain)

    # 3. Resolve TENANT_ADMIN role up-front so we fail fast if seed
    #    data is missing instead of mid-transaction.
    tenant_admin_role = await role_repository.get_by_code(db, "TENANT_ADMIN")
    if tenant_admin_role is None:
        # Should never happen post-migration; treat as 500 by re-raising.
        raise RuntimeError(
            "TENANT_ADMIN role not found — has the role-seed migration run?"
        )

    # 4. Build the rows in a single transaction. Admin email
    #    uniqueness is per-tenant: another tenant having the same email
    #    is fine. Inside this brand-new tenant nothing exists yet, so
    #    the only race is two concurrent create-tenant requests landing
    #    the same email — caught by the IntegrityError fallback below.
    tenant = Tenant(
        code=request.code,
        name=request.name,
        domain=request.domain,
    )
    db.add(tenant)
    try:
        await db.flush()  # populate tenant.id

        admin_user = User(
            tenant_id=tenant.id,
            email=request.initial_admin.email,
            password_hash=hash_password(request.initial_admin.password),
            first_name=request.initial_admin.first_name,
            last_name=request.initial_admin.last_name,
        )
        db.add(admin_user)
        await db.flush()

        db.add(UserRole(user_id=admin_user.id, role_id=tenant_admin_role.id))
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        # Generic mapper — pre-checks should have caught the obvious
        # conflicts; an IntegrityError here is the rare race tail.
        raise TenantCodeAlreadyExistsError(request.code) from exc

    await db.refresh(admin_user, ["roles"])

    # 5. Audit — atomic with the create. Both rows commit together
    #    via the request's Unit of Work, so partial-state audit is
    #    impossible.
    audit_kwargs = {
        "request_id": request_id,
        "ip_address": ip_address,
        "user_agent": user_agent,
    }
    await audit_log_service.log_action(
        db,
        actor_user_id=actor_user_id,
        action="TENANT_CREATED",
        tenant_id=tenant.id,
        resource_type="tenant",
        resource_id=str(tenant.id),
        metadata={
            "code": tenant.code,
            "name": tenant.name,
            "domain": tenant.domain,
            "initial_admin_user_id": str(admin_user.id),
        },
        **audit_kwargs,
    )
    await audit_log_service.log_action(
        db,
        actor_user_id=actor_user_id,
        action="USER_CREATED",
        tenant_id=tenant.id,
        resource_type="user",
        resource_id=str(admin_user.id),
        metadata={
            "email": admin_user.email,
            "scope": "TENANT",
            "tenant_code": tenant.code,
            "role_codes": ["TENANT_ADMIN"],
            "via": "tenant_create",
        },
        **audit_kwargs,
    )
    return tenant, admin_user


async def list_tenants(
    db: AsyncSession,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Tenant], int]:
    """Paginated tenant listing. Returns ``(rows, total)``."""
    return await tenant_repository.list_tenants(
        db, status=status, limit=limit, offset=offset
    )


async def update_tenant(
    db: AsyncSession,
    tenant: Tenant,
    request: TenantUpdateRequest,
    *,
    actor_user_id: uuid.UUID | None,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> Tenant:
    """Apply a PATCH update. Returns the refreshed tenant."""
    changes: dict[str, dict[str, str | None]] = {}

    if request.name is not None and request.name != tenant.name:
        changes["name"] = {"from": tenant.name, "to": request.name}
        tenant.name = request.name

    if request.domain is not None and request.domain != tenant.domain:
        # Re-check uniqueness — the DB will catch a concurrent race
        # via the unique index, but we want a clean 400 in the common
        # case rather than letting the IntegrityError surface.
        existing = await tenant_repository.get_by_domain(db, request.domain)
        if existing is not None and existing.id != tenant.id:
            raise TenantDomainAlreadyExistsError(request.domain)
        changes["domain"] = {"from": tenant.domain, "to": request.domain}
        tenant.domain = request.domain

    if request.status is not None and request.status.value != tenant.status:
        if not _is_valid_transition(tenant.status, request.status.value):
            raise TenantStatusTransitionError(tenant.status, request.status.value)
        changes["status"] = {"from": tenant.status, "to": request.status.value}
        tenant.status = request.status.value

    if not changes:
        # Nothing to do; don't write an audit row for a no-op.
        return tenant

    await db.flush()
    # ``updated_at`` is server-side ``onupdate=func.now()``: SQLAlchemy
    # expires the attribute post-flush and the router's response
    # builder would otherwise trigger a lazy reload outside an awaited
    # session call (MissingGreenlet). Refresh now while still inside a
    # coroutine.
    await db.refresh(tenant, ["updated_at"])

    await audit_log_service.log_action(
        db,
        actor_user_id=actor_user_id,
        action="TENANT_UPDATED",
        tenant_id=tenant.id,
        resource_type="tenant",
        resource_id=str(tenant.id),
        metadata={"changes": changes},
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return tenant
