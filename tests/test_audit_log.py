"""Tests for the audit logging foundation.

End-to-end USER_CREATED coverage lives in ``test_admin_create_user.py``.
This file exercises the auth-side audit rows that come out of login,
failed login, /me, and the role-denied flow.
"""

from __future__ import annotations

import pytest
from app.models.audit_log import AuditLog
from app.services import audit_log_service
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests._helpers import (
    auth_headers,
    create_platform_user,
    create_user_in_new_tenant,
    login_user,
)

DEFAULT_PASSWORD = "supersecret123"
EMAIL = "audit-user@compiq.example.com"


async def _audit_rows(db: AsyncSession, action: str | None = None) -> list[AuditLog]:
    """Fetch audit rows, optionally filtered by action."""
    stmt = select(AuditLog).order_by(AuditLog.created_at)
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# LOGIN_SUCCESS
# ---------------------------------------------------------------------------
async def test_login_success_writes_audit_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await create_platform_user(db_session, email=EMAIL)

    token = await login_user(client, EMAIL)
    assert token

    rows = await _audit_rows(db_session, action="LOGIN_SUCCESS")
    assert len(rows) == 1
    row = rows[0]

    assert str(row.actor_user_id) == user["id"]
    assert row.resource_type == "user"
    assert row.resource_id == user["id"]
    assert row.extra_data is not None
    assert row.extra_data["email"] == EMAIL
    assert "password" not in row.extra_data
    # Token must never appear in audit metadata.
    assert "access_token" not in row.extra_data
    assert "token" not in row.extra_data


# ---------------------------------------------------------------------------
# LOGIN_FAILED
# ---------------------------------------------------------------------------
async def test_login_failure_writes_audit_row_without_password(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Failed login must produce a LOGIN_FAILED row but never store the
    plaintext password attempted."""
    await create_platform_user(db_session, email=EMAIL)

    response = await client.post(
        "/auth/login", json={"email": EMAIL, "password": "wrongpassword"}
    )
    assert response.status_code == 401
    body = response.json()
    assert body["error_code"] == "INVALID_CREDENTIALS"
    assert "wrongpassword" not in body["message"]

    rows = await _audit_rows(db_session, action="LOGIN_FAILED")
    assert len(rows) == 1
    row = rows[0]

    # Operator can see WHY it failed via metadata.
    assert row.extra_data is not None
    assert row.extra_data["user_existed"] is True
    assert row.extra_data["password_matched"] is False
    assert row.extra_data["user_active"] is True
    assert row.extra_data["email_attempted"] == EMAIL

    # Plaintext password must NEVER reach the audit row.
    metadata_blob = str(row.extra_data)
    assert "wrongpassword" not in metadata_blob
    assert DEFAULT_PASSWORD not in metadata_blob


async def test_login_failure_for_unknown_email_writes_anonymous_audit(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """No matching user → audit row with actor_user_id=NULL."""
    response = await client.post(
        "/auth/login",
        json={"email": "ghost@compiq.example.com", "password": DEFAULT_PASSWORD},
    )
    assert response.status_code == 401

    rows = await _audit_rows(db_session, action="LOGIN_FAILED")
    assert len(rows) == 1
    row = rows[0]

    assert row.actor_user_id is None
    assert row.extra_data is not None
    assert row.extra_data["user_existed"] is False


# ---------------------------------------------------------------------------
# CURRENT_USER_VIEWED
# ---------------------------------------------------------------------------
async def test_me_writes_audit_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await create_platform_user(db_session, email=EMAIL)

    headers = await auth_headers(client, EMAIL)
    response = await client.get("/auth/me", headers=headers)
    assert response.status_code == 200

    rows = await _audit_rows(db_session, action="CURRENT_USER_VIEWED")
    assert len(rows) == 1
    assert str(rows[0].actor_user_id) == user["id"]


# ---------------------------------------------------------------------------
# ACCESS_DENIED via require_tenant_roles dependency
# ---------------------------------------------------------------------------
async def test_role_denied_writes_access_denied_audit(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A MANAGER user (inside a tenant) hitting an HR-only tenant-role
    endpoint must produce an ACCESS_DENIED audit row."""
    tenant, _ = await create_user_in_new_tenant(
        db_session,
        email="mgr@acme.example.com",
        tenant_code="acme",
        domain="acme.example.com",
        role="MANAGER",
    )
    # Capture the id eagerly: the upcoming 403 will trip the test
    # client's get_db override into a rollback (mirroring production),
    # and rollback expires every ORM instance in the session. Accessing
    # ``tenant.id`` after that point triggers a lazy reload outside the
    # awaited session call → MissingGreenlet.
    tenant_id = tenant.id

    headers = await auth_headers(client, "mgr@acme.example.com")
    response = await client.get("/auth/admin-test", headers=headers)
    assert response.status_code == 403

    rows = await _audit_rows(db_session, action="ACCESS_DENIED")
    assert len(rows) == 1
    row = rows[0]

    assert row.resource_type == "endpoint"
    assert row.resource_id == "/auth/admin-test"
    assert row.tenant_id == tenant_id
    assert row.extra_data is not None
    assert row.extra_data["scope"] == "TENANT"
    assert "MANAGER" in row.extra_data["user_tenant_roles"]
    assert sorted(row.extra_data["required_roles"]) == ["C_AND_B", "HR"]


# ---------------------------------------------------------------------------
# Failure-tolerant best-effort contract
# ---------------------------------------------------------------------------
async def test_independent_audit_failure_does_not_break_request(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``log_action_independent`` blows up (DB unreachable, session
    factory misconfigured, etc.) the user-facing response must still
    return cleanly. A failed login must look like a failed login — not
    a 500 — to the client.

    Regression for the best-effort contract documented in
    ``audit_log_service``: an audit-pipeline failure is observability
    lost, not a request that 500s.
    """
    await create_platform_user(db_session, email=EMAIL)

    class _ExplodingSession:
        async def __aenter__(self) -> _ExplodingSession:
            raise RuntimeError("simulated audit-DB outage")

        async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
            return None

    def _exploding_factory() -> _ExplodingSession:
        return _ExplodingSession()

    monkeypatch.setattr(
        audit_log_service,
        "_independent_session_factory",
        _exploding_factory,
    )

    # Wrong password -> LOGIN_FAILED path -> log_action_independent.
    response = await client.post(
        "/auth/login", json={"email": EMAIL, "password": "wrongpassword"}
    )

    # The wire-facing response is unchanged.
    assert response.status_code == 401
    body = response.json()
    assert body["error_code"] == "INVALID_CREDENTIALS"

    # The audit row never landed (the independent session blew up), but
    # the suite continues — no 500, no leaked exception.
    rows = await _audit_rows(db_session, action="LOGIN_FAILED")
    assert rows == []
