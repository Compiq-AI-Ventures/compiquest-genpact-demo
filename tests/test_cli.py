"""Tests for the operational CLI.

The CLI's bootstrap function takes an explicit ``AsyncSession``, so
tests can drive it with the suite's own ``db_session`` fixture and
sidestep the ``AsyncSessionLocal`` / event-loop wiring entirely.
The argparse-shaped entry point is verified with a thin smoke test
that monkey-patches the bootstrap coroutine.
"""

from __future__ import annotations

import uuid

import pytest
from app.cli import bootstrap_super_admin
from app.core.security import verify_password
from app.models.audit_log import AuditLog
from app.models.role import Role
from app.models.user_role import UserRole
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
async def test_bootstrap_creates_super_admin_when_none_exists(
    db_session: AsyncSession,
) -> None:
    user = await bootstrap_super_admin(
        db_session,
        email="root@example.com",
        password="bootstrap-pass-12345",
    )

    assert user is not None
    assert user.email == "root@example.com"
    # Password is hashed (never stored plaintext).
    assert user.password_hash != "bootstrap-pass-12345"
    assert verify_password("bootstrap-pass-12345", user.password_hash)


async def test_bootstrap_grants_super_admin_at_platform_level(
    db_session: AsyncSession,
) -> None:
    user = await bootstrap_super_admin(
        db_session,
        email="root@example.com",
        password="bootstrap-pass-12345",
    )
    assert user is not None
    # Platform tier: the user itself has no tenant binding.
    assert user.tenant_id is None

    grants = (
        await db_session.execute(
            select(UserRole).where(UserRole.user_id == user.id)
        )
    ).scalars().all()
    assert len(grants) == 1
    grant = grants[0]
    role = (
        await db_session.execute(select(Role).where(Role.id == grant.role_id))
    ).scalar_one()
    assert role.code == "SUPER_ADMIN"
    # Sanity: the SUPER_ADMIN role is PLATFORM-scope.
    assert role.scope == "PLATFORM"


async def test_bootstrap_writes_user_created_audit_row(
    db_session: AsyncSession,
) -> None:
    user = await bootstrap_super_admin(
        db_session,
        email="root@example.com",
        password="bootstrap-pass-12345",
    )
    assert user is not None

    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "USER_CREATED",
                AuditLog.resource_id == str(user.id),
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    # Bootstrap has no actor.
    assert row.actor_user_id is None
    assert row.tenant_id is None
    assert row.extra_data is not None
    assert row.extra_data["scope"] == "PLATFORM"
    assert row.extra_data["role_codes"] == ["SUPER_ADMIN"]
    # The bootstrap password must NEVER reach the audit row.
    assert "bootstrap-pass-12345" not in str(row.extra_data)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
async def test_bootstrap_is_idempotent(db_session: AsyncSession) -> None:
    """Running twice should leave exactly one super-admin."""
    first = await bootstrap_super_admin(
        db_session,
        email="root@example.com",
        password="bootstrap-pass-12345",
    )
    assert first is not None

    # Second call with the SAME credentials → returns None, no new row.
    second = await bootstrap_super_admin(
        db_session,
        email="root@example.com",
        password="bootstrap-pass-12345",
    )
    assert second is None

    # And: third call with DIFFERENT credentials still returns None.
    # (The CLI is for the FIRST super-admin; subsequent ones go through
    # the API.)
    third = await bootstrap_super_admin(
        db_session,
        email="other@example.com",
        password="different-pass-67890",
    )
    assert third is None

    # Database has exactly one super-admin grant.
    super_admin_role_id = (
        await db_session.execute(select(Role.id).where(Role.code == "SUPER_ADMIN"))
    ).scalar_one()
    grants = (
        await db_session.execute(
            select(UserRole).where(UserRole.role_id == super_admin_role_id)
        )
    ).scalars().all()
    assert len(grants) == 1


# ---------------------------------------------------------------------------
# Bad inputs
# ---------------------------------------------------------------------------
async def test_bootstrap_rejects_short_password(db_session: AsyncSession) -> None:
    """Schema validation is enforced via admin_user_service.create_platform_user
    (which feeds the inputs through ``PlatformUserCreateRequest``; a
    too-short password raises ``pydantic.ValidationError``)."""
    with pytest.raises(ValidationError):
        await bootstrap_super_admin(
            db_session,
            email="root@example.com",
            password="short",
        )


async def test_bootstrap_rejects_invalid_email(db_session: AsyncSession) -> None:
    with pytest.raises(ValidationError):
        await bootstrap_super_admin(
            db_session,
            email="not-an-email",
            password="bootstrap-pass-12345",
        )


# ---------------------------------------------------------------------------
# argparse entry point — smoke test
# ---------------------------------------------------------------------------
def test_main_requires_subcommand() -> None:
    """Calling the CLI with no args exits non-zero (argparse enforces required=True)."""
    from app.cli import main

    with pytest.raises(SystemExit) as excinfo:
        main([])
    # argparse uses 2 for usage errors.
    assert excinfo.value.code == 2


def test_main_rejects_unknown_subcommand() -> None:
    from app.cli import main

    with pytest.raises(SystemExit):
        main(["wrong-command"])


def test_run_bootstrap_returns_2_when_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_run_bootstrap_super_admin`` returns 2 when env vars aren't set."""
    import asyncio

    from app.cli import _run_bootstrap_super_admin

    monkeypatch.delenv("INIT_SUPER_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("INIT_SUPER_ADMIN_PASSWORD", raising=False)

    result = asyncio.run(_run_bootstrap_super_admin())
    assert result == 2


# Touch ``uuid`` to keep the import for any future test that builds
# tenant ids by hand without going through helpers.
_ = uuid
