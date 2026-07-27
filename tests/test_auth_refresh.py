"""Tests for POST /auth/refresh.

Refresh-token rotation properties under test:

* Happy path issues a new access + refresh pair.
* The presented refresh token is single-use — the second time it's
  presented, the request fails with 401.
* Access tokens cannot be used at /auth/refresh (token-type check).
* New access tokens reflect the user's CURRENT roles, not the roles
  baked into the original login token.
* A user whose tenant becomes SUSPENDED cannot refresh (TENANT_INACTIVE
  collapses to INVALID_REFRESH_TOKEN — same anti-leak posture as login).
"""

from __future__ import annotations

from app.core.config import get_settings
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from httpx import AsyncClient
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests._helpers import (
    DEFAULT_PASSWORD,
    create_platform_user,
    create_user_in_new_tenant,
)


async def _login(
    client: AsyncClient, email: str, *, tenant_code: str | None = None
) -> dict[str, str | int]:
    body = {"email": email, "password": DEFAULT_PASSWORD}
    if tenant_code is not None:
        body["tenant_code"] = tenant_code
    response = await client.post("/auth/login", json=body)
    response.raise_for_status()
    return response.json()["data"]


async def test_refresh_returns_new_pair(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await create_platform_user(db_session, email="r@compiq.example.com")
    tokens = await _login(client, "r@compiq.example.com")

    response = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["data"]["access_token"]
    assert body["data"]["refresh_token"]
    # Both tokens must be different from the originals — that's the whole
    # point of rotation.
    assert body["data"]["access_token"] != tokens["access_token"]
    assert body["data"]["refresh_token"] != tokens["refresh_token"]


async def test_refresh_token_is_single_use(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Replaying the same refresh token returns 401."""
    await create_platform_user(db_session, email="rotate@compiq.example.com")
    tokens = await _login(client, "rotate@compiq.example.com")

    first = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert first.status_code == 200

    second = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert second.status_code == 401
    assert second.json()["error_code"] == "INVALID_REFRESH_TOKEN"


async def test_refresh_with_garbage_token_returns_401(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/refresh", json={"refresh_token": "not-a-jwt"}
    )
    assert response.status_code == 401
    assert response.json()["error_code"] == "INVALID_REFRESH_TOKEN"


async def test_refresh_rejects_access_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Presenting an access token at /auth/refresh fails on the type
    check — this is what protects against accidental swaps in client
    code."""
    await create_platform_user(db_session, email="swap@compiq.example.com")
    tokens = await _login(client, "swap@compiq.example.com")

    response = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["access_token"]}
    )
    assert response.status_code == 401
    assert response.json()["error_code"] == "INVALID_REFRESH_TOKEN"


async def test_refresh_picks_up_role_changes_since_login(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Roles in the refreshed access token reflect the DB at refresh time,
    not the snapshot the original login captured.

    Approach: create a tenant user whose initial role is HR. Log in,
    capture the access token's role list (= ['HR']). Manually elevate
    the same user to also hold C_AND_B (still tenant-scoped). Refresh
    — the new access token should include both roles.
    """
    from app.models.role import Role
    from app.models.user import User
    from app.models.user_role import UserRole

    tenant, user = await create_user_in_new_tenant(
        db_session,
        email="elevate@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="HR",
    )
    tokens = await _login(client, "elevate@acme.example.com")

    settings = get_settings()
    pre_claims = jwt.decode(
        tokens["access_token"],
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )
    assert pre_claims["roles"] == ["HR"]
    assert pre_claims["tenant_id"] == str(tenant.id)

    # Grant C_AND_B in addition to HR.
    cnb = (
        await db_session.execute(select(Role).where(Role.code == "C_AND_B"))
    ).scalar_one()
    db_user = (
        await db_session.execute(select(User).where(User.id.is_not(None), User.email == "elevate@acme.example.com"))
    ).scalar_one()
    db_session.add(UserRole(user_id=db_user.id, role_id=cnb.id))
    await db_session.commit()
    _ = user  # not used, but make linter happy

    response = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 200
    new_access = response.json()["data"]["access_token"]
    new_claims = jwt.decode(
        new_access, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
    )

    # New token reflects the new grant; old token never did.
    assert sorted(new_claims["roles"]) == ["C_AND_B", "HR"]
    assert new_claims["tenant_id"] == str(tenant.id)


async def test_refresh_fails_when_tenant_becomes_inactive(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """If a tenant user's tenant is SUSPENDED between login and refresh,
    the refresh fails with INVALID_REFRESH_TOKEN."""
    tenant, _ = await create_user_in_new_tenant(
        db_session,
        email="alice@suspend.example.com",
        tenant_code="suspend",
        domain="suspend.example.com",
    )
    tokens = await _login(client, "alice@suspend.example.com")

    # Suspend the tenant directly in the DB.
    db_tenant = (
        await db_session.execute(select(Tenant).where(Tenant.id == tenant.id))
    ).scalar_one()
    db_tenant.status = "SUSPENDED"
    await db_session.commit()

    response = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 401
    assert response.json()["error_code"] == "INVALID_REFRESH_TOKEN"


async def test_refresh_writes_success_audit_with_rotated_jti(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await create_platform_user(
        db_session, email="audit-r@compiq.example.com"
    )
    tokens = await _login(client, "audit-r@compiq.example.com")

    settings = get_settings()
    old_refresh_jti = jwt.decode(
        tokens["refresh_token"],
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )["jti"]

    response = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 200

    rows = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "REFRESH_SUCCESS")
        )
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert str(row.actor_user_id) == user["id"]
    assert row.extra_data is not None
    assert row.extra_data["rotated_jti"] == old_refresh_jti


async def test_refresh_failed_writes_audit_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(
        "/auth/refresh", json={"refresh_token": "definitely-not-a-jwt"}
    )
    assert response.status_code == 401

    rows = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "REFRESH_FAILED")
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].extra_data is not None
    assert rows[0].extra_data["reason"] == "decode_error"
