"""Tests for the role-based access control machinery.

Single-tenant-per-user model — there's no X-Tenant-ID, no
multi-tenant disambiguation. Authorization rules:

* Platform endpoints require platform-scope roles (caller's tenant_id
  must be NULL).
* Tenant endpoints require tenant-scope roles. The caller is
  implicitly acting in their own tenant; cross-tenant access is
  impossible because the caller cannot supply a different tenant.
* Platform users hitting tenant endpoints get 400 TENANT_CONTEXT_REQUIRED.

Covers both:

* Runtime behavior — the dependency allows / denies based on the
  user's roles, returns the right status codes and error envelope.
* Factory-time validation — ``require_*_roles`` rejects an empty list
  and unknown role codes at import time, not at first request.
"""

from __future__ import annotations

import pytest
from app.dependencies.role_dependency import (
    require_platform_roles,
    require_tenant_roles,
)
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests._helpers import (
    auth_headers,
    create_platform_user,
    create_user_in_new_tenant,
)


# ---------------------------------------------------------------------------
# Platform-role endpoints (/auth/platform-admin-test)
# ---------------------------------------------------------------------------
async def test_super_admin_can_hit_platform_admin_test(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await create_platform_user(
        db_session, email="root@compiq.example.com", role="SUPER_ADMIN"
    )

    headers = await auth_headers(client, "root@compiq.example.com")
    response = await client.get("/auth/platform-admin-test", headers=headers)
    assert response.status_code == 200


async def test_tenant_user_cannot_hit_platform_admin_test(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A user who only holds a TENANT-scope role (HR) cannot access a
    platform-admin endpoint."""
    await create_user_in_new_tenant(
        db_session,
        email="hr@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="HR",
    )

    headers = await auth_headers(client, "hr@acme.example.com")
    response = await client.get("/auth/platform-admin-test", headers=headers)
    assert response.status_code == 403
    body = response.json()
    assert body["error_code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# Tenant-role endpoints (/auth/admin-test, /auth/tenant-admin-test, etc.)
# ---------------------------------------------------------------------------
async def test_hr_user_can_hit_admin_test(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """HR inside the active tenant → 200."""
    tenant, _ = await create_user_in_new_tenant(
        db_session,
        email="hr@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="HR",
    )

    headers = await auth_headers(client, "hr@acme.example.com")
    response = await client.get("/auth/admin-test", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["active_tenant_id"] == str(tenant.id)
    assert body["roles_in_tenant"] == ["HR"]


async def test_tenant_admin_can_hit_tenant_admin_test(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await create_user_in_new_tenant(
        db_session,
        email="ta@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="TENANT_ADMIN",
    )

    headers = await auth_headers(client, "ta@acme.example.com")
    response = await client.get("/auth/tenant-admin-test", headers=headers)
    assert response.status_code == 200


async def test_manager_can_hit_manager_test(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await create_user_in_new_tenant(
        db_session,
        email="mgr@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="MANAGER",
    )

    headers = await auth_headers(client, "mgr@acme.example.com")
    response = await client.get("/auth/manager-test", headers=headers)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Forbidden access — wire-facing 403 envelope
# ---------------------------------------------------------------------------
async def test_cxo_user_cannot_hit_admin_test(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """CXO is NOT in the [HR, C_AND_B] allow-list."""
    await create_user_in_new_tenant(
        db_session,
        email="cxo@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="CXO",
    )

    headers = await auth_headers(client, "cxo@acme.example.com")
    response = await client.get("/auth/admin-test", headers=headers)
    assert response.status_code == 403
    body = response.json()
    assert body["status"] == "fail"
    assert body["error_code"] == "FORBIDDEN"


async def test_no_token_returns_401_not_403(client: AsyncClient) -> None:
    response = await client.get("/auth/admin-test")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Tenant context required — platform user hitting a tenant endpoint
# ---------------------------------------------------------------------------
async def test_platform_user_on_tenant_endpoint_gets_400(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A platform user (no tenant binding) hitting a tenant-scoped
    endpoint gets 400 TENANT_CONTEXT_REQUIRED. Platform admins use
    the /admin/* endpoints with explicit tenant ids in the path for
    cross-tenant work."""
    await create_platform_user(
        db_session, email="root@compiq.example.com", role="SUPER_ADMIN"
    )

    headers = await auth_headers(client, "root@compiq.example.com")
    response = await client.get("/auth/admin-test", headers=headers)
    assert response.status_code == 400
    assert response.json()["error_code"] == "TENANT_CONTEXT_REQUIRED"


# ---------------------------------------------------------------------------
# Cross-tenant isolation — user in tenant A cannot reach tenant B
# ---------------------------------------------------------------------------
async def test_role_in_one_tenant_does_not_leak_to_another(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A user with HR in Acme is a different account from any user in
    Globex. Globex's HR endpoints can only be reached by Globex users.
    With single-tenant-per-user there's nothing to leak — the caller's
    tenant is fixed by ``users.tenant_id``.

    This test demonstrates the property: an Acme HR user hitting the
    HR endpoint succeeds (their tenant) and a Globex user with no HR
    role (just MANAGER) gets 403 from that same endpoint inside Globex."""
    # Acme HR succeeds inside Acme.
    await create_user_in_new_tenant(
        db_session,
        email="hr@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="HR",
    )
    acme_headers = await auth_headers(client, "hr@acme.example.com")
    acme_resp = await client.get("/auth/admin-test", headers=acme_headers)
    assert acme_resp.status_code == 200

    # Globex MANAGER is rejected from /auth/admin-test (HR/C_AND_B only).
    await create_user_in_new_tenant(
        db_session,
        email="mgr@globex.example.com",
        tenant_code="globex",
        domain="globex.example.com",
        role="MANAGER",
    )
    globex_headers = await auth_headers(client, "mgr@globex.example.com")
    globex_resp = await client.get("/auth/admin-test", headers=globex_headers)
    assert globex_resp.status_code == 403


# ---------------------------------------------------------------------------
# Factory-time validation (sync tests — pytest-asyncio leaves these alone)
# ---------------------------------------------------------------------------
def test_require_platform_roles_rejects_empty_list() -> None:
    with pytest.raises(ValueError, match="at least one role"):
        require_platform_roles([])


def test_require_tenant_roles_rejects_empty_list() -> None:
    with pytest.raises(ValueError, match="at least one role"):
        require_tenant_roles([])


def test_require_platform_roles_rejects_unknown_code() -> None:
    with pytest.raises(ValueError, match="unknown role"):
        require_platform_roles(["NOT_A_REAL_ROLE"])


def test_require_tenant_roles_rejects_unknown_code() -> None:
    with pytest.raises(ValueError, match="unknown role"):
        require_tenant_roles(["NOT_A_REAL_ROLE"])
