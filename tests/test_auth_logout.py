"""Tests for POST /auth/logout and the JWT deny-list.

After logout, the access token used on the request is rejected by
:func:`app.dependencies.auth_dependency.get_current_user` on every
subsequent request.
"""

from __future__ import annotations

from app.models.audit_log import AuditLog
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests._helpers import auth_headers, create_platform_user, login_user


async def test_logout_revokes_token_for_subsequent_requests(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The access token used to log out is rejected on the next call."""
    await create_platform_user(db_session, email="bye@compiq.example.com")
    headers = await auth_headers(client, "bye@compiq.example.com")

    # Token works before logout.
    me_before = await client.get("/auth/me", headers=headers)
    assert me_before.status_code == 200

    logout_resp = await client.post("/auth/logout", headers=headers)
    assert logout_resp.status_code == 200
    body = logout_resp.json()
    assert body["status"] == "success"
    assert body["data"]["revoked"] is True

    # Same token should now be rejected.
    me_after = await client.get("/auth/me", headers=headers)
    assert me_after.status_code == 401


async def test_logout_writes_audit_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await create_platform_user(db_session, email="bye@compiq.example.com")
    headers = await auth_headers(client, "bye@compiq.example.com")

    response = await client.post("/auth/logout", headers=headers)
    assert response.status_code == 200

    rows = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "LOGOUT")
        )
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert str(row.actor_user_id) == user["id"]
    assert row.extra_data is not None
    assert row.extra_data["revoked"] is True
    # jti recorded so operators can correlate with the deny-list entry.
    assert isinstance(row.extra_data["jti"], str)


async def test_logout_without_token_returns_401(client: AsyncClient) -> None:
    response = await client.post("/auth/logout")
    assert response.status_code == 401


async def test_other_users_tokens_are_unaffected_by_logout(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Revoking Alice's token must not impact Bob's session."""
    await create_platform_user(db_session, email="alice@compiq.example.com")
    await create_platform_user(db_session, email="bob@compiq.example.com")

    alice_headers = await auth_headers(client, "alice@compiq.example.com")
    bob_headers = await auth_headers(client, "bob@compiq.example.com")

    await client.post("/auth/logout", headers=alice_headers)

    # Bob's token still works.
    me_bob = await client.get("/auth/me", headers=bob_headers)
    assert me_bob.status_code == 200

    # Alice's token does not.
    me_alice = await client.get("/auth/me", headers=alice_headers)
    assert me_alice.status_code == 401


async def test_logout_is_idempotent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Calling logout twice with the same token returns 401 the second time
    (because the token is now revoked) but the first call still succeeded."""
    await create_platform_user(db_session, email="twice@compiq.example.com")
    token = await login_user(client, "twice@compiq.example.com")
    headers = {"Authorization": f"Bearer {token}"}

    first = await client.post("/auth/logout", headers=headers)
    assert first.status_code == 200

    # Second call: get_current_user rejects the now-revoked token, so
    # the route never executes — that's the expected behaviour.
    second = await client.post("/auth/logout", headers=headers)
    assert second.status_code == 401


async def test_refresh_token_cannot_authenticate_against_protected_routes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Defense-in-depth: a refresh token must NOT pass as an access token."""
    await create_platform_user(db_session, email="r@compiq.example.com")
    response = await client.post(
        "/auth/login",
        json={"email": "r@compiq.example.com", "password": "supersecret123"},
    )
    refresh_token = response.json()["data"]["refresh_token"]

    headers = {"Authorization": f"Bearer {refresh_token}"}
    me = await client.get("/auth/me", headers=headers)
    assert me.status_code == 401
