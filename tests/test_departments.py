"""Tests for the first business module — departments.

Single-tenant-per-user model
----------------------------
Every tenant user implicitly acts inside their own tenant. There's no
``X-Tenant-ID`` header to switch contexts. Cross-tenant tests use
*different* users in different tenants.

Covers:

* CRUD via the route layer (HTTP).
* Authorization: read = any tenant member, write = TENANT_ADMIN/HR.
* Cross-tenant isolation through the API (HR in tenant A can't see
  departments in tenant B).
* Platform users (no tenant binding) get 400 TENANT_CONTEXT_REQUIRED
  on tenant-scoped endpoints.
* RLS isolation at the DB level — direct SQL with ``app.current_tenant``
  set hides the other tenant's rows even when the application
  filtering is bypassed.
"""

from __future__ import annotations

import uuid

from app.models.tenant import Tenant
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests._helpers import (
    auth_headers,
    create_platform_user,
    create_tenant,
    create_user_in_new_tenant,
)

VALID_BODY: dict = {
    "code": "ENG",
    "name": "Engineering",
    "description": "Builds the product.",
}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
async def test_create_department_succeeds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    tenant, _ = await create_user_in_new_tenant(
        db_session,
        email="hr@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="HR",
    )

    headers = await auth_headers(client, "hr@acme.example.com")
    response = await client.post("/departments", json=VALID_BODY, headers=headers)
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["code"] == "ENG"
    assert data["tenant_id"] == str(tenant.id)


async def test_create_department_lowercases_code_to_uppercase(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await create_user_in_new_tenant(
        db_session,
        email="hr@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="HR",
    )

    headers = await auth_headers(client, "hr@acme.example.com")
    response = await client.post(
        "/departments", json={**VALID_BODY, "code": "eng"}, headers=headers
    )
    assert response.status_code == 201
    assert response.json()["data"]["code"] == "ENG"


async def test_create_department_duplicate_code_returns_400(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await create_user_in_new_tenant(
        db_session,
        email="hr@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="HR",
    )

    headers = await auth_headers(client, "hr@acme.example.com")
    first = await client.post("/departments", json=VALID_BODY, headers=headers)
    assert first.status_code == 201
    second = await client.post("/departments", json=VALID_BODY, headers=headers)
    assert second.status_code == 400
    assert second.json()["error_code"] == "DEPARTMENT_CODE_ALREADY_EXISTS"


async def test_create_department_as_non_admin_returns_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A MANAGER (not in [TENANT_ADMIN, HR]) cannot create departments."""
    await create_user_in_new_tenant(
        db_session,
        email="mgr@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="MANAGER",
    )

    headers = await auth_headers(client, "mgr@acme.example.com")
    response = await client.post("/departments", json=VALID_BODY, headers=headers)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Read (list + get)
# ---------------------------------------------------------------------------
async def test_list_departments_returns_only_callers_tenant_rows(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Two HR users in two different tenants — each sees only their
    own tenant's departments."""
    await create_user_in_new_tenant(
        db_session,
        email="hr@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="HR",
    )
    await create_user_in_new_tenant(
        db_session,
        email="hr@globex.example.com",
        tenant_code="globex",
        domain="globex.example.com",
        role="HR",
    )

    h_acme = await auth_headers(client, "hr@acme.example.com")
    h_globex = await auth_headers(client, "hr@globex.example.com")

    await client.post(
        "/departments", json={**VALID_BODY, "code": "ENG"}, headers=h_acme
    )
    await client.post(
        "/departments", json={**VALID_BODY, "code": "SALES"}, headers=h_globex
    )

    list_acme = await client.get("/departments", headers=h_acme)
    codes_acme = [d["code"] for d in list_acme.json()["data"]["items"]]
    assert codes_acme == ["ENG"]

    list_globex = await client.get("/departments", headers=h_globex)
    codes_globex = [d["code"] for d in list_globex.json()["data"]["items"]]
    assert codes_globex == ["SALES"]


async def test_get_department_in_other_tenant_returns_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A department's UUID belongs to one tenant. Asking for it from
    another tenant's user returns 404 (not 403 — we don't even
    acknowledge the row exists)."""
    await create_user_in_new_tenant(
        db_session,
        email="hr@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="HR",
    )
    h_acme = await auth_headers(client, "hr@acme.example.com")
    create = await client.post("/departments", json=VALID_BODY, headers=h_acme)
    dept_id = create.json()["data"]["id"]

    await create_user_in_new_tenant(
        db_session,
        email="hr@globex.example.com",
        tenant_code="globex",
        domain="globex.example.com",
        role="HR",
    )
    h_globex = await auth_headers(client, "hr@globex.example.com")

    response = await client.get(f"/departments/{dept_id}", headers=h_globex)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Update + delete
# ---------------------------------------------------------------------------
async def test_patch_department_renames(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await create_user_in_new_tenant(
        db_session,
        email="hr@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="HR",
    )
    headers = await auth_headers(client, "hr@acme.example.com")

    create = await client.post("/departments", json=VALID_BODY, headers=headers)
    dept_id = create.json()["data"]["id"]

    response = await client.patch(
        f"/departments/{dept_id}",
        json={"name": "Engineering — renamed"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["name"] == "Engineering — renamed"


async def test_delete_department(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await create_user_in_new_tenant(
        db_session,
        email="hr@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="HR",
    )
    headers = await auth_headers(client, "hr@acme.example.com")

    create = await client.post("/departments", json=VALID_BODY, headers=headers)
    dept_id = create.json()["data"]["id"]

    delete = await client.delete(f"/departments/{dept_id}", headers=headers)
    assert delete.status_code == 204

    follow_up = await client.get(f"/departments/{dept_id}", headers=headers)
    assert follow_up.status_code == 404


# ---------------------------------------------------------------------------
# Tenant context required
# ---------------------------------------------------------------------------
async def test_platform_user_on_tenant_endpoint_returns_400(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A platform user (no tenant binding) hits a tenant-scoped
    endpoint → 400 TENANT_CONTEXT_REQUIRED. Platform admins use the
    /admin endpoints with explicit tenant ids in the path for cross-
    tenant work."""
    await create_platform_user(db_session, email="root@compiq.example.com")
    headers = await auth_headers(client, "root@compiq.example.com")

    response = await client.get("/departments", headers=headers)
    assert response.status_code == 400
    assert response.json()["error_code"] == "TENANT_CONTEXT_REQUIRED"


# ---------------------------------------------------------------------------
# RLS isolation — direct SQL, not via the API
# ---------------------------------------------------------------------------
async def test_rls_blocks_cross_tenant_select(
    db_session: AsyncSession, test_engine
) -> None:
    """Defense-in-depth proof: even if the application code skipped its
    explicit ``WHERE tenant_id = ...`` filter, RLS would still hide
    rows from other tenants.

    This test runs raw SQL on a fresh session that does NOT have
    ``app.platform_override`` set, then proves the other tenant's
    department is invisible.
    """
    acme = await create_tenant(
        db_session, code="acme", domain="acme-rls.example.com"
    )
    globex = await create_tenant(
        db_session, code="globex", domain="globex-rls.example.com"
    )
    await db_session.commit()

    await db_session.execute(
        text(
            "INSERT INTO departments (id, tenant_id, code, name) "
            "VALUES (gen_random_uuid(), :tid, 'ENG', 'Engineering')"
        ),
        {"tid": str(acme.id)},
    )
    await db_session.execute(
        text(
            "INSERT INTO departments (id, tenant_id, code, name) "
            "VALUES (gen_random_uuid(), :tid, 'ENG', 'Engineering')"
        ),
        {"tid": str(globex.id)},
    )
    await db_session.commit()

    # Open a fresh, unprivileged session — no platform_override.
    # NB: Postgres ``SET <name> = $1`` is rejected as a syntax error
    # because ``SET`` doesn't accept bind parameters. Use the
    # ``set_config(name, value, is_local)`` built-in instead, which is
    # an ordinary function call and therefore parameter-friendly.
    #
    # Also: tests connect as the ``postgres`` superuser, and Postgres
    # superusers (and BYPASSRLS roles) bypass RLS unconditionally —
    # ``FORCE ROW LEVEL SECURITY`` only constrains the table owner,
    # not superusers. To make this a real defense-in-depth proof we
    # ``SET ROLE`` to the unprivileged ``rls_tester`` role provisioned
    # by ``conftest.test_engine``. The role has SELECT on
    # ``departments`` but no superuser / BYPASSRLS attribute, so RLS
    # actually applies to the queries below.
    factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    async with factory() as unprivileged:
        await unprivileged.execute(text("SET ROLE rls_tester"))
        await unprivileged.execute(
            text("SELECT set_config('app.current_tenant', :tid, false)"),
            {"tid": str(acme.id)},
        )

        result = await unprivileged.execute(text("SELECT tenant_id FROM departments"))
        visible_tenant_ids = {row[0] for row in result.all()}
        assert visible_tenant_ids == {acme.id}

        await unprivileged.execute(
            text("SELECT set_config('app.current_tenant', :tid, false)"),
            {"tid": str(globex.id)},
        )
        result = await unprivileged.execute(text("SELECT tenant_id FROM departments"))
        visible_tenant_ids = {row[0] for row in result.all()}
        assert visible_tenant_ids == {globex.id}

        await unprivileged.execute(text("RESET app.current_tenant"))
        result = await unprivileged.execute(text("SELECT COUNT(*) FROM departments"))
        assert result.scalar_one() == 0


_ = uuid  # keep import alive
_ = Tenant  # keep import alive
_ = select  # keep import alive
