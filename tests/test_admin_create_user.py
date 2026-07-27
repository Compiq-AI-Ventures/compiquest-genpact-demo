"""Tests for admin-driven user provisioning endpoints.

Covers:

* ``POST /admin/users``                — platform user creation.
* ``POST /admin/tenants/{id}/users``   — tenant user creation.
* Authorization: who can call each endpoint, and what gets a 403.
* Scope validation: PLATFORM endpoint rejects TENANT roles and vice-versa.
* Per-tenant email uniqueness: same email is OK in two different tenants.
* Audit: USER_CREATED rows for both flows, tenant_id where applicable.
"""

from __future__ import annotations

import uuid

from app.models.audit_log import AuditLog
from app.models.user_role import UserRole
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests._helpers import (
    DEFAULT_PASSWORD,
    auth_headers,
    create_platform_user,
    create_tenant,
    create_user_in_new_tenant,
)

# ---------------------------------------------------------------------------
# Tiny helpers
# ---------------------------------------------------------------------------
PLATFORM_BODY = {
    "email": "new-platform@compiq.example.com",
    "password": "supersecret123",
    "first_name": "New",
    "last_name": "Platform",
    "role_codes": ["PLATFORM_ADMIN"],
}

TENANT_BODY = {
    "email": "new-tenant@acme.example.com",
    "password": "supersecret123",
    "first_name": "New",
    "last_name": "Tenant",
    "role_codes": ["HR"],
}


async def _bootstrap_super_admin(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    email: str = "root@compiq.example.com",
) -> dict[str, str]:
    """Create a SUPER_ADMIN platform user, log in, return Auth header."""
    await create_platform_user(db_session, email=email, role="SUPER_ADMIN")
    return await auth_headers(client, email)


# ---------------------------------------------------------------------------
# POST /admin/users — happy path + audit
# ---------------------------------------------------------------------------
async def test_create_platform_user_succeeds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _bootstrap_super_admin(client, db_session)

    response = await client.post("/admin/users", json=PLATFORM_BODY, headers=headers)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "success"
    assert body["data"]["email"] == PLATFORM_BODY["email"]
    assert body["data"]["roles"] == ["PLATFORM_ADMIN"]
    assert body["data"]["tenant_id"] is None  # platform user


async def test_create_platform_user_writes_audit_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _bootstrap_super_admin(client, db_session)

    response = await client.post("/admin/users", json=PLATFORM_BODY, headers=headers)
    assert response.status_code == 201
    new_user_id = response.json()["data"]["id"]

    rows = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "USER_CREATED")
        )
    ).scalars().all()
    matching = [r for r in rows if r.resource_id == new_user_id]
    assert len(matching) == 1
    row = matching[0]
    assert row.tenant_id is None
    assert row.extra_data is not None
    assert row.extra_data["scope"] == "PLATFORM"
    assert row.extra_data["role_codes"] == ["PLATFORM_ADMIN"]
    # Sensitive fields must NEVER appear.
    assert "password" not in row.extra_data
    assert PLATFORM_BODY["password"] not in str(row.extra_data)


# ---------------------------------------------------------------------------
# POST /admin/users — authorization
# ---------------------------------------------------------------------------
async def test_create_platform_user_unauthenticated_returns_401(
    client: AsyncClient,
) -> None:
    response = await client.post("/admin/users", json=PLATFORM_BODY)
    assert response.status_code == 401


async def test_create_platform_user_as_tenant_admin_returns_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A TENANT_ADMIN of some tenant cannot create platform users."""
    await create_user_in_new_tenant(
        db_session,
        email="tadmin@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="TENANT_ADMIN",
    )
    headers = await auth_headers(client, "tadmin@acme.example.com")
    response = await client.post(
        "/admin/users", json=PLATFORM_BODY, headers=headers
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# POST /admin/users — scope mismatch
# ---------------------------------------------------------------------------
async def test_create_platform_user_rejects_tenant_role(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The platform endpoint must reject TENANT-scope codes."""
    headers = await _bootstrap_super_admin(client, db_session)

    response = await client.post(
        "/admin/users",
        json={**PLATFORM_BODY, "role_codes": ["HR"]},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "ROLE_SCOPE_MISMATCH"


async def test_create_platform_user_with_unknown_role_returns_400(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _bootstrap_super_admin(client, db_session)

    response = await client.post(
        "/admin/users",
        json={**PLATFORM_BODY, "role_codes": ["DEVELOPER"]},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "INVALID_ROLE_CODE"


# ---------------------------------------------------------------------------
# POST /admin/users — duplicate email
# ---------------------------------------------------------------------------
async def test_create_platform_user_duplicate_email_returns_400(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _bootstrap_super_admin(client, db_session)

    first = await client.post("/admin/users", json=PLATFORM_BODY, headers=headers)
    assert first.status_code == 201

    second = await client.post("/admin/users", json=PLATFORM_BODY, headers=headers)
    assert second.status_code == 400
    body = second.json()
    assert body["error_code"] == "EMAIL_ALREADY_EXISTS"
    # Duplicate-detection message must NOT echo the email.
    assert PLATFORM_BODY["email"] not in body["message"]


# ---------------------------------------------------------------------------
# POST /admin/tenants/{tenant_id}/users — happy path + tenant_id audit
# ---------------------------------------------------------------------------
async def test_create_tenant_user_by_platform_admin_succeeds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Platform admin override — can administer any tenant."""
    headers = await _bootstrap_super_admin(client, db_session)

    tenant = await create_tenant(db_session, code="acme", domain="acme.example.com")
    await db_session.commit()

    response = await client.post(
        f"/admin/tenants/{tenant.id}/users",
        json=TENANT_BODY,
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["data"]["email"] == TENANT_BODY["email"]
    assert body["data"]["roles"] == ["HR"]
    assert body["data"]["tenant_id"] == str(tenant.id)

    # The audit row should carry the tenant_id.
    new_user_id = body["data"]["id"]
    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "USER_CREATED",
                AuditLog.resource_id == new_user_id,
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].tenant_id == tenant.id
    assert rows[0].extra_data["scope"] == "TENANT"
    assert rows[0].extra_data["tenant_code"] == "acme"

    # The user should hold a single grant — no tenant_id on the row anymore.
    grants = (
        await db_session.execute(
            select(UserRole).where(UserRole.user_id == uuid.UUID(new_user_id))
        )
    ).scalars().all()
    assert len(grants) == 1


async def test_create_tenant_user_by_tenant_admin_succeeds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """TENANT_ADMIN of tenant X can create users inside tenant X."""
    tenant, _ = await create_user_in_new_tenant(
        db_session,
        email="tadmin@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="TENANT_ADMIN",
    )
    headers = await auth_headers(client, "tadmin@acme.example.com")

    response = await client.post(
        f"/admin/tenants/{tenant.id}/users",
        json=TENANT_BODY,
        headers=headers,
    )
    assert response.status_code == 201


# ---------------------------------------------------------------------------
# Per-tenant email uniqueness — same email in two tenants is FINE
# ---------------------------------------------------------------------------
async def test_same_email_in_two_tenants_succeeds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The same email may appear in many different tenants. Email is
    only globally unique at the platform tier."""
    headers = await _bootstrap_super_admin(client, db_session)

    acme = await create_tenant(db_session, code="acme", domain="acme.example.com")
    globex = await create_tenant(db_session, code="globex", domain="globex.example.com")
    await db_session.commit()

    body = {**TENANT_BODY, "email": "alice@hr.com"}

    r1 = await client.post(
        f"/admin/tenants/{acme.id}/users", json=body, headers=headers
    )
    assert r1.status_code == 201, r1.text

    r2 = await client.post(
        f"/admin/tenants/{globex.id}/users", json=body, headers=headers
    )
    assert r2.status_code == 201, r2.text

    # And a SECOND attempt inside Acme is rejected — uniqueness is
    # per-tenant, not none-tenant.
    r3 = await client.post(
        f"/admin/tenants/{acme.id}/users", json=body, headers=headers
    )
    assert r3.status_code == 400
    assert r3.json()["error_code"] == "EMAIL_ALREADY_EXISTS"


# ---------------------------------------------------------------------------
# POST /admin/tenants/{tenant_id}/users — authorization + scope
# ---------------------------------------------------------------------------
async def test_create_tenant_user_by_non_admin_returns_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """An ordinary HR user cannot provision new tenant users."""
    tenant, _ = await create_user_in_new_tenant(
        db_session,
        email="hr@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="HR",
    )
    headers = await auth_headers(client, "hr@acme.example.com")
    response = await client.post(
        f"/admin/tenants/{tenant.id}/users",
        json=TENANT_BODY,
        headers=headers,
    )
    assert response.status_code == 403


async def test_create_tenant_user_for_unknown_tenant_returns_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _bootstrap_super_admin(client, db_session)
    fake_id = uuid.uuid4()

    response = await client.post(
        f"/admin/tenants/{fake_id}/users",
        json=TENANT_BODY,
        headers=headers,
    )
    assert response.status_code == 404
    assert response.json()["error_code"] == "NOT_FOUND"


async def test_create_tenant_user_rejects_platform_role(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The tenant endpoint must reject PLATFORM-scope codes."""
    headers = await _bootstrap_super_admin(client, db_session)

    tenant = await create_tenant(db_session, code="acme", domain="acme.example.com")
    await db_session.commit()

    response = await client.post(
        f"/admin/tenants/{tenant.id}/users",
        json={**TENANT_BODY, "role_codes": ["PLATFORM_ADMIN"]},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "ROLE_SCOPE_MISMATCH"


# ---------------------------------------------------------------------------
# Tenant admin in tenant A cannot create users in tenant B
# ---------------------------------------------------------------------------
async def test_tenant_admin_cannot_administer_another_tenant(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A TENANT_ADMIN of acme cannot create users inside globex."""
    await create_user_in_new_tenant(
        db_session,
        email="tadmin@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="TENANT_ADMIN",
    )
    other = await create_tenant(
        db_session, code="globex", domain="globex.example.com"
    )
    await db_session.commit()

    headers = await auth_headers(client, "tadmin@acme.example.com")
    response = await client.post(
        f"/admin/tenants/{other.id}/users",
        json=TENANT_BODY,
        headers=headers,
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------
async def test_create_platform_user_short_password_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _bootstrap_super_admin(client, db_session)

    response = await client.post(
        "/admin/users",
        json={**PLATFORM_BODY, "password": "short"},
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


async def test_create_platform_user_empty_role_codes_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _bootstrap_super_admin(client, db_session)

    response = await client.post(
        "/admin/users",
        json={**PLATFORM_BODY, "role_codes": []},
        headers=headers,
    )
    assert response.status_code == 422


_ = DEFAULT_PASSWORD  # keep import alive for shared password constant
