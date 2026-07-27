"""Tests for the tenant lifecycle admin API.

Coverage:

* ``POST   /admin/tenants``    happy path, duplicate code, duplicate
                               domain, schema validation, authorization
                               (platform admin only), strict domain regex.
* ``GET    /admin/tenants``    listing with pagination + status filter,
                               SUPPORT_ADMIN read access.
* ``GET    /admin/tenants/{}`` happy path, 404.
* ``PATCH  /admin/tenants/{}`` rename, status transitions (and the
                               DISABLED→* rejection), domain uniqueness
                               on update.
* SUSPENDED tenant blocks normal users; platform admin can still
  administer.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
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
# Helpers
# ---------------------------------------------------------------------------
async def _platform_admin_headers(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    role_code: str = "PLATFORM_ADMIN",
    email: str = "platform@compiq.example.com",
) -> dict[str, str]:
    await create_platform_user(db_session, email=email, role=role_code)
    return await auth_headers(client, email)


def _create_body(
    *,
    code: str = "acme",
    domain: str | None = None,
    admin_email: str = "admin@acme.example.com",
) -> dict:
    return {
        "name": code.title(),
        "code": code,
        "domain": domain or f"{code}.example.com",
        "initial_admin": {
            "email": admin_email,
            "password": "supersecret123",
            "first_name": "Bootstrap",
            "last_name": "Admin",
        },
    }


# ---------------------------------------------------------------------------
# POST /admin/tenants — happy path
# ---------------------------------------------------------------------------
async def test_create_tenant_succeeds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _platform_admin_headers(client, db_session)

    body = _create_body()
    response = await client.post("/admin/tenants", json=body, headers=headers)
    assert response.status_code == 201, response.text

    data = response.json()["data"]
    assert data["tenant"]["code"] == "acme"
    assert data["tenant"]["name"] == "Acme"
    assert data["tenant"]["domain"] == "acme.example.com"
    assert data["tenant"]["status"] == "ACTIVE"
    assert data["admin"]["email"] == "admin@acme.example.com"
    # The bootstrap admin should hold TENANT_ADMIN and be bound to the tenant.
    assert data["admin"]["roles"] == ["TENANT_ADMIN"]
    assert data["admin"]["tenant_id"] == data["tenant"]["id"]


async def test_create_tenant_writes_audit_rows(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _platform_admin_headers(client, db_session)

    response = await client.post(
        "/admin/tenants", json=_create_body(), headers=headers
    )
    assert response.status_code == 201
    tenant_id = response.json()["data"]["tenant"]["id"]

    # TENANT_CREATED row.
    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "TENANT_CREATED",
                AuditLog.resource_id == tenant_id,
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].tenant_id == uuid.UUID(tenant_id)
    assert rows[0].extra_data["code"] == "acme"

    # USER_CREATED row for the bootstrap admin (with via=tenant_create).
    user_rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "USER_CREATED",
                AuditLog.tenant_id == uuid.UUID(tenant_id),
            )
        )
    ).scalars().all()
    assert len(user_rows) == 1
    assert user_rows[0].extra_data["via"] == "tenant_create"


async def test_create_tenant_lowercases_code(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _platform_admin_headers(client, db_session)

    body = _create_body(code="ACME")
    response = await client.post("/admin/tenants", json=body, headers=headers)
    assert response.status_code == 201
    assert response.json()["data"]["tenant"]["code"] == "acme"


# ---------------------------------------------------------------------------
# POST /admin/tenants — failure paths
# ---------------------------------------------------------------------------
async def test_create_tenant_duplicate_code_returns_400(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _platform_admin_headers(client, db_session)

    first = await client.post("/admin/tenants", json=_create_body(), headers=headers)
    assert first.status_code == 201

    # Same code, different domain (so the failure is about the code).
    second = await client.post(
        "/admin/tenants",
        json=_create_body(domain="acme2.example.com", admin_email="another@x.example.com"),
        headers=headers,
    )
    assert second.status_code == 400
    assert second.json()["error_code"] == "TENANT_CODE_ALREADY_EXISTS"


async def test_create_tenant_duplicate_domain_returns_400(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Two tenants cannot share the same domain — domain is the
    canonical email/SSO routing anchor and must be globally unique."""
    headers = await _platform_admin_headers(client, db_session)

    first = await client.post("/admin/tenants", json=_create_body(), headers=headers)
    assert first.status_code == 201

    # Same domain, different code. ``_create_body`` derives ``domain``
    # from ``code`` when no explicit domain is given, so without an
    # explicit ``domain=`` the second tenant would end up with
    # ``acme2.example.com`` and the duplicate-domain check would have
    # nothing to catch.
    second = await client.post(
        "/admin/tenants",
        json=_create_body(
            code="acme2",
            domain="acme.example.com",
            admin_email="another@acme.example.com",
        ),
        headers=headers,
    )
    assert second.status_code == 400
    assert second.json()["error_code"] == "TENANT_DOMAIN_ALREADY_EXISTS"


async def test_create_tenant_invalid_domain_regex_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Domain must satisfy the multi-label DNS-style regex."""
    headers = await _platform_admin_headers(client, db_session)

    body = _create_body(domain="not a domain")
    response = await client.post("/admin/tenants", json=body, headers=headers)
    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


async def test_create_tenant_unauthenticated_returns_401(client: AsyncClient) -> None:
    response = await client.post("/admin/tenants", json=_create_body())
    assert response.status_code == 401


async def test_create_tenant_as_tenant_admin_returns_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A tenant's own admin can't create new tenants."""
    await create_user_in_new_tenant(
        db_session,
        email="tadmin@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="TENANT_ADMIN",
    )
    headers = await auth_headers(client, "tadmin@acme.example.com")

    # Try a brand-new tenant body (not 'acme', so we don't hit the
    # uniqueness check first).
    response = await client.post(
        "/admin/tenants",
        json=_create_body(code="globex", domain="globex.example.com"),
        headers=headers,
    )
    assert response.status_code == 403


async def test_create_tenant_invalid_code_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _platform_admin_headers(client, db_session)

    body = _create_body()
    body["code"] = "Has Spaces!"
    response = await client.post("/admin/tenants", json=body, headers=headers)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /admin/tenants — listing
# ---------------------------------------------------------------------------
async def test_list_tenants_paginates(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _platform_admin_headers(client, db_session)

    for code in ("acme", "globex", "initech"):
        await create_tenant(db_session, code=code, domain=f"{code}.example.com")
    await db_session.commit()

    response = await client.get("/admin/tenants?limit=2&offset=0", headers=headers)
    assert response.status_code == 200
    page = response.json()["data"]
    assert page["total"] == 3
    assert len(page["items"]) == 2
    # Default order is by code → first page should start at "acme".
    assert page["items"][0]["code"] == "acme"


async def test_list_tenants_filter_by_status(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _platform_admin_headers(client, db_session)

    await create_tenant(db_session, code="active1", domain="active1.example.com")
    susp = await create_tenant(
        db_session, code="suspended1", domain="suspended1.example.com"
    )
    susp.status = "SUSPENDED"
    await db_session.commit()

    response = await client.get("/admin/tenants?status=SUSPENDED", headers=headers)
    assert response.status_code == 200
    page = response.json()["data"]
    assert page["total"] == 1
    assert page["items"][0]["code"] == "suspended1"


async def test_list_tenants_as_support_admin_succeeds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """SUPPORT_ADMIN gets read access without write access."""
    headers = await _platform_admin_headers(
        client,
        db_session,
        role_code="SUPPORT_ADMIN",
        email="support@compiq.example.com",
    )
    response = await client.get("/admin/tenants", headers=headers)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /admin/tenants/{id}
# ---------------------------------------------------------------------------
async def test_get_tenant_succeeds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _platform_admin_headers(client, db_session)
    tenant = await create_tenant(db_session, code="acme", domain="acme.example.com")
    await db_session.commit()

    response = await client.get(f"/admin/tenants/{tenant.id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["code"] == "acme"
    assert response.json()["data"]["domain"] == "acme.example.com"


async def test_get_tenant_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _platform_admin_headers(client, db_session)
    response = await client.get(f"/admin/tenants/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /admin/tenants/{id}
# ---------------------------------------------------------------------------
async def test_patch_tenant_renames(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _platform_admin_headers(client, db_session)
    tenant = await create_tenant(
        db_session, code="acme", name="Old Name", domain="acme.example.com"
    )
    await db_session.commit()

    response = await client.patch(
        f"/admin/tenants/{tenant.id}",
        json={"name": "New Name"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["name"] == "New Name"


async def test_patch_tenant_domain_to_unique_value(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _platform_admin_headers(client, db_session)
    tenant = await create_tenant(db_session, code="acme", domain="acme.example.com")
    await db_session.commit()

    response = await client.patch(
        f"/admin/tenants/{tenant.id}",
        json={"domain": "acme.io"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["domain"] == "acme.io"


async def test_patch_tenant_domain_to_taken_value_returns_400(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Updating a tenant's domain to one already owned by another
    tenant fails with TENANT_DOMAIN_ALREADY_EXISTS."""
    headers = await _platform_admin_headers(client, db_session)
    other = await create_tenant(
        db_session, code="other", domain="other.example.com"
    )
    target = await create_tenant(
        db_session, code="target", domain="target.example.com"
    )
    await db_session.commit()
    _ = other

    response = await client.patch(
        f"/admin/tenants/{target.id}",
        json={"domain": "other.example.com"},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "TENANT_DOMAIN_ALREADY_EXISTS"


async def test_patch_tenant_status_active_to_suspended(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _platform_admin_headers(client, db_session)
    tenant = await create_tenant(db_session, code="acme", domain="acme.example.com")
    await db_session.commit()

    response = await client.patch(
        f"/admin/tenants/{tenant.id}",
        json={"status": "SUSPENDED"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "SUSPENDED"


async def test_patch_tenant_status_disabled_is_terminal(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """DISABLED → ACTIVE must be rejected."""
    headers = await _platform_admin_headers(client, db_session)
    tenant = await create_tenant(db_session, code="acme", domain="acme.example.com")
    tenant.status = "DISABLED"
    await db_session.commit()

    response = await client.patch(
        f"/admin/tenants/{tenant.id}",
        json={"status": "ACTIVE"},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "TENANT_STATUS_TRANSITION_INVALID"


# ---------------------------------------------------------------------------
# Tenant status enforcement on normal user access
# ---------------------------------------------------------------------------
async def test_suspended_tenant_blocks_normal_user_access(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """An HR user in a SUSPENDED tenant gets 403 from tenant-scoped endpoints."""
    tenant, _ = await create_user_in_new_tenant(
        db_session,
        email="hr@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="HR",
    )
    db_tenant = (
        await db_session.execute(select(Tenant).where(Tenant.id == tenant.id))
    ).scalar_one()
    db_tenant.status = "SUSPENDED"
    await db_session.commit()

    headers = await auth_headers(client, "hr@acme.example.com")
    response = await client.get("/auth/admin-test", headers=headers)
    assert response.status_code == 403
    assert response.json()["error_code"] == "TENANT_INACTIVE"


async def test_login_to_suspended_tenant_returns_inactive_on_first_protected_call(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A user whose tenant is SUSPENDED can still receive a token at
    login (no leak about which tenants are active). The first
    protected call gets ``TENANT_INACTIVE``."""
    tenant, _ = await create_user_in_new_tenant(
        db_session,
        email="stranded@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="HR",
    )
    db_tenant = (
        await db_session.execute(select(Tenant).where(Tenant.id == tenant.id))
    ).scalar_one()
    db_tenant.status = "SUSPENDED"
    await db_session.commit()

    response = await client.post(
        "/auth/login",
        json={
            "email": "stranded@acme.example.com",
            "password": DEFAULT_PASSWORD,
        },
    )
    assert response.status_code == 200
    token = response.json()["data"]["access_token"]
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    # /auth/me itself doesn't enforce tenant status (it's an identity
    # endpoint) — but a tenant-scoped endpoint will.
    assert me.status_code == 200

    admin_test = await client.get(
        "/auth/admin-test", headers={"Authorization": f"Bearer {token}"}
    )
    assert admin_test.status_code == 403
    assert admin_test.json()["error_code"] == "TENANT_INACTIVE"


async def test_platform_admin_can_administer_suspended_tenant(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """SUSPENDED tenants are still administrable via the admin
    endpoints — that's how operators recover from a suspension."""
    headers = await _platform_admin_headers(client, db_session)
    tenant = await create_tenant(db_session, code="acme", domain="acme.example.com")
    tenant.status = "SUSPENDED"
    await db_session.commit()

    response = await client.patch(
        f"/admin/tenants/{tenant.id}",
        json={"status": "ACTIVE"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ACTIVE"


_: Any = DEFAULT_PASSWORD  # keep import alive for shared password constant
