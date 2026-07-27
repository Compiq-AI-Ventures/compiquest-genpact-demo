"""Tests for GET /auth/me + the auth dependency that powers it.

Single-tenant model — the response shape collapses:

* Platform user → ``tenant`` is ``None``, ``roles`` lists PLATFORM-scope codes.
* Tenant user   → ``tenant`` is the user's tenant summary, ``roles``
  lists TENANT-scope codes.

Covers the full failure surface of ``get_current_user``:

* No header                  → 401
* Wrong scheme               → 401
* Garbage token              → 401
* Tampered token             → 401 (signature failure)
* Expired token              → 401
* sub claim missing          → 401
* sub not a UUID             → 401
* Valid token, user deleted  → 401
* Valid token, user inactive → 403  (different from /login — the token
                                     itself is valid; the account is
                                     just disabled)
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from app.core.config import get_settings
from app.core.security import create_access_token
from app.models.user import User
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests._helpers import (
    auth_headers,
    create_platform_user,
    create_user_in_new_tenant,
)


# ---------------------------------------------------------------------------
# Happy path — platform user
# ---------------------------------------------------------------------------
async def test_me_for_platform_user_returns_null_tenant(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await create_platform_user(db_session, email="root@compiq.example.com")
    headers = await auth_headers(client, "root@compiq.example.com")

    response = await client.get("/auth/me", headers=headers)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["status"] == "success"
    data = body["data"]
    assert data["id"] == user["id"]
    assert data["email"] == "root@compiq.example.com"
    # New shape: single ``tenant`` (null for platform user) + flat
    # ``roles``. The old multi-tenant fields are gone.
    assert data["tenant"] is None
    assert data["roles"] == ["SUPER_ADMIN"]
    assert "platform_roles" not in data
    assert "tenant_roles" not in data
    assert "tenants" not in data
    assert "active_tenant_id" not in data
    # Sensitive fields never appear.
    assert "password" not in data
    assert "password_hash" not in data


# ---------------------------------------------------------------------------
# Happy path — tenant user
# ---------------------------------------------------------------------------
async def test_me_for_tenant_user_returns_tenant_summary(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A user bound to a tenant surfaces the tenant + role list."""
    tenant, _ = await create_user_in_new_tenant(
        db_session,
        email="alice@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="HR",
    )
    headers = await auth_headers(client, "alice@acme.example.com")

    response = await client.get("/auth/me", headers=headers)
    assert response.status_code == 200, response.text

    data = response.json()["data"]
    assert data["roles"] == ["HR"]
    assert data["tenant"] == {
        "id": str(tenant.id),
        "code": "acme",
        "name": "Acme",
        "domain": "acme.example.com",
        "status": "ACTIVE",
    }


# ---------------------------------------------------------------------------
# Missing / malformed Authorization
# ---------------------------------------------------------------------------
async def test_me_without_header_returns_401(client: AsyncClient) -> None:
    response = await client.get("/auth/me")
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


async def test_me_with_wrong_scheme_returns_401(client: AsyncClient) -> None:
    """``Authorization: Token <jwt>`` is not Bearer; HTTPBearer rejects it."""
    response = await client.get("/auth/me", headers={"Authorization": "Token deadbeef"})
    assert response.status_code == 401


async def test_me_with_garbage_token_returns_401(client: AsyncClient) -> None:
    response = await client.get(
        "/auth/me", headers={"Authorization": "Bearer this.is.not.a.jwt"}
    )
    assert response.status_code == 401


async def test_me_with_tampered_token_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Flip a character in the signature — the verify step must reject."""
    await create_platform_user(db_session, email="alice@compiq.example.com")
    headers = await auth_headers(client, "alice@compiq.example.com")
    real = headers["Authorization"].removeprefix("Bearer ")

    # Mutate a character in the MIDDLE of the signature. Mutating the
    # very last character can be silently absorbed by base64url's
    # bit-padding: if the signature length isn't 3-byte-aligned, the
    # last character only encodes 2 or 4 *significant* bits — the
    # remaining bits are padding the decoder ignores. So flipping
    # base64url 'A' (000000) to 'B' (000001) at the end can decode to
    # the same byte sequence (and the same signature passes verify).
    # A character in the middle has all 6 bits significant, so the
    # flip is guaranteed to change a decoded byte.
    header_payload, sep, sig = real.rpartition(".")
    mid = len(sig) // 2
    mutated = sig[:mid] + ("A" if sig[mid] != "A" else "B") + sig[mid + 1:]
    tampered = header_payload + sep + mutated

    response = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {tampered}"}
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Expired token
# ---------------------------------------------------------------------------
async def test_me_with_expired_token_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Sign a token whose ``exp`` is already in the past."""
    user = await create_platform_user(db_session, email="alice@compiq.example.com")
    expired = create_access_token(
        data={"sub": user["id"], "email": user["email"], "roles": user["roles"]},
        expires_delta=timedelta(seconds=-1),
    )
    response = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {expired}"}
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Bad / missing claims
# ---------------------------------------------------------------------------
async def test_me_with_missing_sub_returns_401(client: AsyncClient) -> None:
    """Token signed correctly but lacks the ``sub`` claim."""
    from jose import jwt

    settings = get_settings()
    bad = jwt.encode(
        {"email": "x@example.com", "roles": ["HR"]},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    response = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {bad}"}
    )
    assert response.status_code == 401


async def test_me_with_non_uuid_sub_returns_401(client: AsyncClient) -> None:
    from jose import jwt

    settings = get_settings()
    bad = jwt.encode(
        {"sub": "not-a-uuid", "email": "x@example.com", "roles": ["HR"]},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    response = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {bad}"}
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Valid signature, but the user no longer exists
# ---------------------------------------------------------------------------
async def test_me_with_valid_token_for_deleted_user_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await create_platform_user(db_session, email="alice@compiq.example.com")
    headers = await auth_headers(client, "alice@compiq.example.com")

    # Hard-delete the user row. The token is still cryptographically
    # valid but ``get_current_user`` will not find a matching user.
    await db_session.execute(delete(User).where(User.id == uuid.UUID(user["id"])))
    await db_session.commit()

    response = await client.get("/auth/me", headers=headers)
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Valid signature, but the user is inactive → 403
# ---------------------------------------------------------------------------
async def test_me_with_inactive_user_returns_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await create_platform_user(db_session, email="alice@compiq.example.com")
    headers = await auth_headers(client, "alice@compiq.example.com")

    # Toggle is_active off in the DB.
    db_user = (
        await db_session.execute(select(User).where(User.id == uuid.UUID(user["id"])))
    ).scalar_one()
    db_user.is_active = False
    await db_session.commit()

    response = await client.get("/auth/me", headers=headers)
    assert response.status_code == 403
    assert response.json()["error_code"] == "FORBIDDEN"
