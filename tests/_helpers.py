"""Shared utilities for the test suite.

The leading underscore signals "not a test module" so pytest's
collection skips it. Test files import from here directly:

    from tests._helpers import (
        create_platform_user, create_tenant_user, create_tenant,
        login_user, auth_headers,
    )

Production user creation goes through the admin API; for test SETUP
this module bypasses the API and writes users + tenants directly via
the session. That's faster (no admin bootstrap dance) and decouples
"the test needs a user" from "we're testing the admin API". The admin
API has its own dedicated coverage in ``test_admin_create_user.py``.

Single-tenant-per-user model
----------------------------
A user belongs to exactly one tenant or to no tenant. That's reflected
in the helpers: there's no ``setup_tenant_user`` joining a user to a
tenant after the fact, because membership is set at creation time on
``user.tenant_id``. The two creation paths are
:func:`create_platform_user` and :func:`create_tenant_user`.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.security import hash_password
from app.models.role import Role
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user_role import UserRole
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_PASSWORD = "supersecret123"


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------
async def create_tenant(
    db_session: AsyncSession,
    *,
    code: str = "acme",
    name: str | None = None,
    domain: str | None = None,
) -> Tenant:
    """Insert a tenant directly. Returns the persisted row.

    ``domain`` defaults to ``"<code>.example.com"`` so callers don't
    have to think about it for tests that don't care. Provide your own
    when the test depends on a specific domain (e.g. login resolution).
    """
    tenant = Tenant(
        code=code,
        name=name or code.title(),
        domain=domain or f"{code}.example.com",
    )
    db_session.add(tenant)
    await db_session.flush()
    return tenant


def _user_to_dict(user: User, role_codes: list[str]) -> dict[str, Any]:
    """Project a freshly-created ``User`` into the dict shape that older
    tests expect (mirrors ``UserResponse.from_user``)."""
    return {
        "id": str(user.id),
        "tenant_id": str(user.tenant_id) if user.tenant_id is not None else None,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "roles": role_codes,
    }


async def _grant_role(
    db_session: AsyncSession, user: User, role_code: str
) -> None:
    role = (
        await db_session.execute(select(Role).where(Role.code == role_code))
    ).scalar_one()
    db_session.add(UserRole(user_id=user.id, role_id=role.id))


async def create_platform_user(
    db_session: AsyncSession,
    *,
    email: str = "platform@example.com",
    password: str = DEFAULT_PASSWORD,
    first_name: str = "Platform",
    last_name: str | None = "User",
    role: str = "SUPER_ADMIN",
) -> dict[str, Any]:
    """Create a platform user (``tenant_id IS NULL``) with one role grant.

    ``role`` must be a PLATFORM-scope code (SUPER_ADMIN, PLATFORM_ADMIN,
    SUPPORT_ADMIN). The grant is enforced only by service-layer code in
    production; here we trust the caller to pick a sensible role.
    """
    user = User(
        tenant_id=None,
        email=email,
        password_hash=hash_password(password),
        first_name=first_name,
        last_name=last_name,
    )
    db_session.add(user)
    await db_session.flush()
    await _grant_role(db_session, user, role)
    await db_session.commit()
    await db_session.refresh(user)
    return _user_to_dict(user, [role])


async def create_tenant_user(
    db_session: AsyncSession,
    tenant: Tenant,
    *,
    email: str = "user@example.com",
    password: str = DEFAULT_PASSWORD,
    first_name: str = "Test",
    last_name: str | None = "User",
    role: str = "HR",
) -> dict[str, Any]:
    """Create a user bound to ``tenant`` with one TENANT-scope role grant."""
    user = User(
        tenant_id=tenant.id,
        email=email,
        password_hash=hash_password(password),
        first_name=first_name,
        last_name=last_name,
    )
    db_session.add(user)
    await db_session.flush()
    await _grant_role(db_session, user, role)
    await db_session.commit()
    await db_session.refresh(user)
    return _user_to_dict(user, [role])


# ---------------------------------------------------------------------------
# Login helpers
# ---------------------------------------------------------------------------
async def login_user(
    client: AsyncClient,
    email: str,
    password: str = DEFAULT_PASSWORD,
    *,
    tenant_code: str | None = None,
) -> str:
    """Log in and return the bare access_token string.

    Pass ``tenant_code`` for tenant users whose email is not in their
    tenant's domain (the only case where domain-based resolution can't
    find the user). Platform users and in-domain tenant users don't
    need it.
    """
    body: dict[str, Any] = {"email": email, "password": password}
    if tenant_code is not None:
        body["tenant_code"] = tenant_code
    response = await client.post("/auth/login", json=body)
    response.raise_for_status()
    return response.json()["data"]["access_token"]


async def auth_headers(
    client: AsyncClient,
    email: str,
    password: str = DEFAULT_PASSWORD,
    *,
    tenant_code: str | None = None,
) -> dict[str, str]:
    """Login + return ``Authorization`` header dict."""
    token = await login_user(client, email, password, tenant_code=tenant_code)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Convenience compositions
# ---------------------------------------------------------------------------
async def create_user_in_new_tenant(
    db_session: AsyncSession,
    *,
    email: str,
    role: str = "HR",
    tenant_code: str = "acme",
    domain: str | None = None,
    password: str = DEFAULT_PASSWORD,
) -> tuple[Tenant, dict[str, Any]]:
    """Spin up a tenant + a user inside it in one call.

    Common in tests that don't care about reusing tenants across cases.
    Returns ``(tenant, user_dict)``.
    """
    tenant = await create_tenant(
        db_session, code=tenant_code, domain=domain
    )
    user = await create_tenant_user(
        db_session, tenant, email=email, password=password, role=role
    )
    return tenant, user


# ---------------------------------------------------------------------------
# Misc cleanup helpers (kept around for tests that reset state)
# ---------------------------------------------------------------------------
async def revoke_user_grants(
    db_session: AsyncSession, user_id: uuid.UUID | str
) -> None:
    """Drop every UserRole grant for ``user_id``."""
    user_uuid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(user_id)
    await db_session.execute(
        delete(UserRole).where(UserRole.user_id == user_uuid)
    )
    await db_session.commit()
