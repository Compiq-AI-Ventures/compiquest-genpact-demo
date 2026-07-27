"""Seed the database for the load-test harness.

Creates a single tenant ``loadtest`` and ``N`` users, each holding
``TENANT_ADMIN`` so they can exercise every department / admin path
the locustfile probes. Idempotent: rerunning drops the existing
``loadtest`` tenant first (via ON DELETE CASCADE the dependent users,
roles, and departments go with it), then recreates everything from
scratch. That keeps the starting state identical between runs so
result comparisons are meaningful.

Credentials follow a predictable convention so the locustfile can
pick them up without out-of-band coordination::

    email    : loadtest_user_001@loadtest.example.com .. loadtest_user_NNN@...
    password : loadtest-pass-12345

Note on the bcrypt shortcut
---------------------------
bcrypt's cost factor is intentionally slow (that's the whole point), so
naively hashing N passwords during seed makes the script take tens of
seconds for moderate N. Every load-test user shares the *same*
password, so we hash it ONCE and reuse the resulting hash string for
every user — bcrypt's embedded salt means every stored hash is
nominally "the same", but `verify_password` against the real password
still works. This is a deliberate choice for load-testing only; never
do this in production.

Run after the schema is up::

    uv run python -m scripts.seed_loadtest          # default N=200
    uv run python -m scripts.seed_loadtest --users 50
"""

from __future__ import annotations

import argparse
import asyncio

from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.models.role import Role
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user_role import UserRole
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

LOAD_TENANT_CODE = "loadtest"
LOAD_TENANT_NAME = "Loadtest"
LOAD_TENANT_DOMAIN = "loadtest.example.com"
LOAD_PASSWORD = "loadtest-pass-12345"


def email_for(index: int) -> str:
    """Predictable email for the ``index``-th load-test user (1-based)."""
    return f"loadtest_user_{index:03d}@{LOAD_TENANT_DOMAIN}"


async def _drop_tenant_if_exists(db: AsyncSession) -> None:
    """Delete the loadtest tenant, cascading to users/roles/departments."""
    existing = (
        await db.execute(select(Tenant).where(Tenant.code == LOAD_TENANT_CODE))
    ).scalar_one_or_none()
    if existing is None:
        return
    await db.execute(delete(Tenant).where(Tenant.id == existing.id))
    await db.flush()


async def _resolve_tenant_admin_role(db: AsyncSession) -> Role:
    role = (
        await db.execute(select(Role).where(Role.code == "TENANT_ADMIN"))
    ).scalar_one_or_none()
    if role is None:
        raise RuntimeError(
            "TENANT_ADMIN role not found. Is the migration applied? Run "
            "`uv run alembic upgrade head` and try again."
        )
    return role


async def seed(db: AsyncSession, *, user_count: int) -> tuple[Tenant, list[User]]:
    """Seed the loadtest tenant + ``user_count`` users. Returns the new objects."""
    await _drop_tenant_if_exists(db)

    role = await _resolve_tenant_admin_role(db)

    tenant = Tenant(
        code=LOAD_TENANT_CODE,
        name=LOAD_TENANT_NAME,
        domain=LOAD_TENANT_DOMAIN,
        status="ACTIVE",
    )
    db.add(tenant)
    await db.flush()

    # One hash, reused — see module docstring for the rationale.
    shared_hash = hash_password(LOAD_PASSWORD)

    users: list[User] = []
    for i in range(1, user_count + 1):
        user = User(
            tenant_id=tenant.id,
            email=email_for(i),
            password_hash=shared_hash,
            first_name="Load",
            last_name=f"Test{i:03d}",
        )
        db.add(user)
        users.append(user)
    # Flush all users so they get IDs.
    await db.flush()

    # Grant TENANT_ADMIN to every seeded user.
    for user in users:
        db.add(UserRole(user_id=user.id, role_id=role.id))
    await db.flush()

    await db.commit()
    return tenant, users


async def _main(user_count: int) -> int:
    async with AsyncSessionLocal() as db:
        try:
            tenant, users = await seed(db, user_count=user_count)
        except Exception as exc:
            await db.rollback()
            print(f"ERROR: seed failed: {exc}")
            return 1

    print(
        f"Seeded tenant {tenant.code!r} (id={tenant.id}) with "
        f"{len(users)} users."
    )
    print(f"  domain   : {tenant.domain}")
    print(f"  password : {LOAD_PASSWORD}")
    print(f"  emails   : {email_for(1)} .. {email_for(user_count)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed the database for the Locust load-test harness."
    )
    parser.add_argument(
        "--users",
        type=int,
        default=200,
        help="Number of load-test users to create (default: 200).",
    )
    args = parser.parse_args(argv)
    if args.users < 1:
        parser.error("--users must be >= 1")
    return asyncio.run(_main(args.users))


if __name__ == "__main__":
    raise SystemExit(main())
