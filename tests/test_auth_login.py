"""Tests for POST /auth/login.

Single-tenant-per-user model — login resolution flavours:

* Platform user (``users.tenant_id IS NULL``): email is globally unique
  in the platform tier; ``tenant_code`` not needed.
* In-domain tenant user: email's domain matches ``tenants.domain`` →
  resolved automatically; ``tenant_code`` not needed.
* Out-of-domain tenant user: must supply ``tenant_code`` to disambiguate.

Covers all three resolution paths plus failure modes (wrong password,
unknown email, inactive user, mismatched tenant_code) collapsing into
a single 401 ``INVALID_CREDENTIALS`` (no enumeration leak).
"""

from __future__ import annotations

from app.core.config import get_settings
from app.models.user import User
from httpx import AsyncClient
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests._helpers import (
    DEFAULT_PASSWORD,
    create_platform_user,
    create_tenant,
    create_tenant_user,
    create_user_in_new_tenant,
    login_user,
)


# ---------------------------------------------------------------------------
# Happy path — platform user
# ---------------------------------------------------------------------------
async def test_login_platform_user_returns_envelope_and_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await create_platform_user(db_session, email="root@compiq.example.com")

    response = await client.post(
        "/auth/login",
        json={"email": "root@compiq.example.com", "password": DEFAULT_PASSWORD},
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["status"] == "success"
    assert body["message"] == "Login successful"
    assert body["data"]["token_type"] == "bearer"
    assert body["data"]["access_token"]
    assert "." in body["data"]["access_token"]  # JWT has dots
    assert body["data"]["refresh_token"]
    assert "." in body["data"]["refresh_token"]
    assert body["data"]["refresh_token"] != body["data"]["access_token"]
    assert isinstance(body["data"]["expires_in"], int)
    assert body["data"]["expires_in"] > 0
    assert user["email"] == "root@compiq.example.com"


async def test_login_in_domain_tenant_user_resolves_via_email_domain(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """alice@acme.example.com routes to tenant whose domain = acme.example.com."""
    tenant, user = await create_user_in_new_tenant(
        db_session,
        email="alice@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="HR",
    )

    response = await client.post(
        "/auth/login",
        json={"email": "alice@acme.example.com", "password": DEFAULT_PASSWORD},
    )
    assert response.status_code == 200, response.text

    body = response.json()["data"]
    settings = get_settings()
    claims = jwt.decode(
        body["access_token"], settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
    )

    assert claims["sub"] == user["id"]
    assert claims["email"] == "alice@acme.example.com"
    assert claims["tenant_id"] == str(tenant.id)
    assert claims["roles"] == ["HR"]


async def test_login_out_of_domain_tenant_user_requires_tenant_code(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A tenant user whose email domain does NOT match their tenant's
    must supply ``tenant_code`` explicitly. Without it, login fails."""
    tenant, _ = await create_user_in_new_tenant(
        db_session,
        email="alice@gmail.com",       # consultant email, not in tenant domain
        tenant_code="acme",
        domain="acme.example.com",
        role="TENANT_ADMIN",
    )

    # Without tenant_code: domain resolution fails (no tenant has
    # gmail.com), platform-user lookup also fails — 401.
    no_code = await client.post(
        "/auth/login",
        json={"email": "alice@gmail.com", "password": DEFAULT_PASSWORD},
    )
    assert no_code.status_code == 401
    assert no_code.json()["error_code"] == "INVALID_CREDENTIALS"

    # With tenant_code: succeeds.
    with_code = await client.post(
        "/auth/login",
        json={
            "email": "alice@gmail.com",
            "password": DEFAULT_PASSWORD,
            "tenant_code": "acme",
        },
    )
    assert with_code.status_code == 200
    settings = get_settings()
    claims = jwt.decode(
        with_code.json()["data"]["access_token"],
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )
    assert claims["tenant_id"] == str(tenant.id)


async def test_login_same_email_in_multiple_tenants_resolves_per_domain(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Two tenants can each have ``alice@hr.com``-style email when using
    their own domain. Domain-based login routes each Alice to her own
    tenant."""
    acme, acme_alice = await create_user_in_new_tenant(
        db_session,
        email="alice@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="HR",
    )
    globex, globex_alice = await create_user_in_new_tenant(
        db_session,
        email="alice@globex.example.com",
        tenant_code="globex",
        domain="globex.example.com",
        role="HR",
    )
    assert acme.id != globex.id
    assert acme_alice["id"] != globex_alice["id"]

    settings = get_settings()
    for email, expected_tenant in (
        ("alice@acme.example.com", acme),
        ("alice@globex.example.com", globex),
    ):
        token = await login_user(client, email)
        claims = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        assert claims["tenant_id"] == str(expected_tenant.id)


# ---------------------------------------------------------------------------
# Token shape
# ---------------------------------------------------------------------------
async def test_login_access_and_refresh_tokens_have_correct_types(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Access token must claim ``type=access``; refresh token ``type=refresh``."""
    await create_platform_user(db_session, email="typed@compiq.example.com")

    response = await client.post(
        "/auth/login",
        json={"email": "typed@compiq.example.com", "password": DEFAULT_PASSWORD},
    )
    body = response.json()["data"]

    settings = get_settings()
    access_claims = jwt.decode(
        body["access_token"], settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
    )
    refresh_claims = jwt.decode(
        body["refresh_token"], settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
    )

    assert access_claims["type"] == "access"
    assert refresh_claims["type"] == "refresh"
    # Refresh token must NOT carry roles/email — those are re-derived
    # from the DB at refresh time.
    assert "roles" not in refresh_claims
    assert "email" not in refresh_claims
    assert "tenant_id" not in refresh_claims
    # jti differs so they can be revoked independently.
    assert access_claims["jti"] != refresh_claims["jti"]


async def test_login_token_carries_expected_claims(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await create_platform_user(db_session, email="bob@compiq.example.com")
    token = await login_user(client, "bob@compiq.example.com")

    settings = get_settings()
    claims = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])

    assert claims["sub"] == user["id"]
    assert claims["email"] == "bob@compiq.example.com"
    # Single-tenant claim shape: tenant_id (null for platform user) +
    # flat roles list. The previous {"platform_roles": [...],
    # "tenant_roles": {tid: [...]}} layout is gone.
    assert claims["tenant_id"] is None
    assert claims["roles"] == ["SUPER_ADMIN"]
    # jti is a hex UUID; verify it's present and looks right.
    assert isinstance(claims["jti"], str)
    assert len(claims["jti"]) == 32
    # iat / exp are Unix timestamps (ints once decoded).
    assert isinstance(claims["iat"], int)
    assert isinstance(claims["exp"], int)
    assert claims["exp"] > claims["iat"]


async def test_login_email_lookup_is_case_insensitive(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A user registered as alice@... should be able to log in as ALICE@..."""
    await create_platform_user(db_session, email="alice@compiq.example.com")

    response = await client.post(
        "/auth/login",
        json={"email": "ALICE@COMPIQ.EXAMPLE.COM", "password": DEFAULT_PASSWORD},
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Wrong password — 401, no enumeration leak
# ---------------------------------------------------------------------------
async def test_login_wrong_password_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await create_platform_user(db_session, email="alice@compiq.example.com")

    response = await client.post(
        "/auth/login",
        json={"email": "alice@compiq.example.com", "password": "wrongpassword"},
    )
    assert response.status_code == 401
    body = response.json()
    assert body["status"] == "fail"
    assert body["error_code"] == "INVALID_CREDENTIALS"
    assert "wrongpassword" not in body["message"]
    # WWW-Authenticate header per RFC 6750.
    assert response.headers.get("www-authenticate") == "Bearer"


# ---------------------------------------------------------------------------
# Unknown email — same 401 / message as wrong password (anti-enumeration)
# ---------------------------------------------------------------------------
async def test_login_unknown_email_matches_wrong_password_response(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await create_platform_user(db_session, email="alice@compiq.example.com")

    wrong_pwd = await client.post(
        "/auth/login",
        json={"email": "alice@compiq.example.com", "password": "wrongpassword"},
    )
    unknown = await client.post(
        "/auth/login",
        json={"email": "ghost@compiq.example.com", "password": "supersecret123"},
    )

    assert wrong_pwd.status_code == unknown.status_code == 401
    assert wrong_pwd.json()["error_code"] == unknown.json()["error_code"]
    assert wrong_pwd.json()["message"] == unknown.json()["message"]


# ---------------------------------------------------------------------------
# Inactive user — STILL 401 from /login (no enumeration leak)
# ---------------------------------------------------------------------------
async def test_login_inactive_user_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await create_platform_user(
        db_session, email="inactive@compiq.example.com"
    )

    # Mark inactive directly in the DB.
    db_user = (
        await db_session.execute(
            select(User).where(User.email == "inactive@compiq.example.com")
        )
    ).scalar_one()
    db_user.is_active = False
    await db_session.commit()

    response = await client.post(
        "/auth/login",
        json={"email": "inactive@compiq.example.com", "password": DEFAULT_PASSWORD},
    )
    assert response.status_code == 401
    body = response.json()
    # Same generic message as wrong password — no leak that the account
    # exists but is disabled.
    assert body["error_code"] == "INVALID_CREDENTIALS"
    assert "inactive" not in body["message"].lower()
    assert user["email"] not in body["message"]


# ---------------------------------------------------------------------------
# tenant_code resolution edge cases
# ---------------------------------------------------------------------------
async def test_login_with_email_domain_that_matches_no_tenant_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """If the email's domain doesn't resolve to any tenant AND no
    matching platform user exists, login collapses to
    INVALID_CREDENTIALS — same shape as a wrong password (no
    enumeration leak about whether the domain is known)."""
    # An Acme user exists, but we attempt login as a user whose email
    # is in a totally unrelated domain.
    await create_user_in_new_tenant(
        db_session,
        email="alice@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
    )

    response = await client.post(
        "/auth/login",
        json={"email": "stranger@unknown.example.com", "password": DEFAULT_PASSWORD},
    )
    assert response.status_code == 401
    assert response.json()["error_code"] == "INVALID_CREDENTIALS"


async def test_login_with_unknown_tenant_code_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A tenant_code that doesn't exist collapses to INVALID_CREDENTIALS
    (no leak about which codes are real)."""
    await create_user_in_new_tenant(
        db_session,
        email="alice@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
    )
    response = await client.post(
        "/auth/login",
        json={
            "email": "alice@acme.example.com",
            "password": DEFAULT_PASSWORD,
            "tenant_code": "no-such-tenant",
        },
    )
    assert response.status_code == 401
    assert response.json()["error_code"] == "INVALID_CREDENTIALS"


async def test_login_platform_user_wins_over_tenant_email_domain_match(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """If a platform user and a tenant user share the same email and
    the email's domain happens to map to a tenant, the platform user
    is resolved first (per the documented order)."""
    # Two users with the same email — one platform-tier, one in a
    # tenant whose domain matches the email's domain.
    await create_platform_user(
        db_session, email="alice@compiq.example.com", password="PlatformPass1!"
    )
    await create_tenant(db_session, code="compiq", domain="compiq.example.com")
    # Drop into the just-created tenant (different password so we can
    # tell them apart by which one accepts the login).
    tenant_obj = (
        await db_session.execute(
            select(__import__("app.models.tenant", fromlist=["Tenant"]).Tenant).where(
                __import__("app.models.tenant", fromlist=["Tenant"]).Tenant.code == "compiq"
            )
        )
    ).scalar_one()
    await create_tenant_user(
        db_session,
        tenant_obj,
        email="alice@compiq.example.com",
        password="TenantPass1!",
        role="HR",
    )

    # Platform password wins.
    plat = await client.post(
        "/auth/login",
        json={"email": "alice@compiq.example.com", "password": "PlatformPass1!"},
    )
    assert plat.status_code == 200

    # The tenant Alice is reachable only with explicit tenant_code.
    tenant = await client.post(
        "/auth/login",
        json={
            "email": "alice@compiq.example.com",
            "password": "TenantPass1!",
            "tenant_code": "compiq",
        },
    )
    assert tenant.status_code == 200


# ---------------------------------------------------------------------------
# Schema validation → 422 envelope
# ---------------------------------------------------------------------------
async def test_login_invalid_email_returns_422(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/login",
        json={"email": "not-an-email", "password": DEFAULT_PASSWORD},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


async def test_login_short_password_returns_422(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/login",
        json={"email": "alice@example.com", "password": "short"},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


async def test_login_missing_email_returns_422(client: AsyncClient) -> None:
    response = await client.post("/auth/login", json={"password": DEFAULT_PASSWORD})
    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"
