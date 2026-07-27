"""Operational CLI.

Run via:

    uv run compiqcorebe <subcommand>          # uses pyproject script entry
    uv run python -m app.cli <subcommand>     # equivalent

Subcommands
-----------

* ``bootstrap-super-admin``
    Creates the very first ``SUPER_ADMIN`` if none exists. Reads
    ``INIT_SUPER_ADMIN_EMAIL`` and ``INIT_SUPER_ADMIN_PASSWORD`` from
    the environment. Idempotent — running it again when a super-admin
    already exists prints a notice and exits 0.

This module deliberately keeps zero business logic. It composes
existing services (``admin_user_service.create_platform_user``) so the
CLI and the HTTP API converge on the same code path.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.role import Role
from app.models.user import User
from app.models.user_role import UserRole
from app.schemas.admin_user_schema import AdminCreatePlatformUserRequest
from app.services import admin_user_service


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
async def _super_admin_already_exists(db: AsyncSession) -> bool:
    """Return True if any platform user holds the SUPER_ADMIN role.

    Single-tenant-per-user model: "platform user" means
    ``users.tenant_id IS NULL``; the role grant itself no longer
    carries a tenant id.
    """
    stmt = (
        select(User.id)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(Role.code == "SUPER_ADMIN")
        .where(User.tenant_id.is_(None))
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.first() is not None


async def bootstrap_super_admin(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    first_name: str = "Super",
    last_name: str = "Admin",
) -> User | None:
    """Create a SUPER_ADMIN if none exists.

    Returns the newly-created :class:`User`, or ``None`` if a
    super-admin was already present (idempotent no-op).

    Raises whatever ``admin_user_service.create_platform_user`` raises
    on bad input — a duplicate email surfaces as
    :class:`EmailAlreadyExistsError`, etc.
    """
    if await _super_admin_already_exists(db):
        return None

    request = AdminCreatePlatformUserRequest(
        email=email,
        password=password,
        first_name=first_name,
        last_name=last_name,
        role_codes=["SUPER_ADMIN"],
    )
    # ``actor_user_id=None`` — there's nobody to attribute the action to
    # at bootstrap. The audit row records the creation with an
    # explicit ``via=bootstrap_cli`` marker.
    return await admin_user_service.create_platform_user(
        db,
        request,
        actor_user_id=None,
    )


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
async def _run_bootstrap_super_admin() -> int:
    email = os.environ.get("INIT_SUPER_ADMIN_EMAIL", "").strip()
    password = os.environ.get("INIT_SUPER_ADMIN_PASSWORD", "")

    if not email or not password:
        print(
            "ERROR: INIT_SUPER_ADMIN_EMAIL and INIT_SUPER_ADMIN_PASSWORD must be set.",
            file=sys.stderr,
        )
        return 2

    async with AsyncSessionLocal() as db:
        try:
            user = await bootstrap_super_admin(db, email=email, password=password)
            # Services flush only — they expect their caller to own the
            # transaction. In production the request's ``get_db`` commits
            # on success; here the CLI is the unit-of-work owner, so we
            # commit ourselves.
            await db.commit()
        except Exception as exc:
            await db.rollback()
            print(f"ERROR: bootstrap failed: {exc}", file=sys.stderr)
            return 1

    if user is None:
        print("A SUPER_ADMIN already exists; nothing to do.")
    else:
        print(f"Created SUPER_ADMIN: {user.email} (id={user.id})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="compiqcorebe",
        description="CompIQCoreBe operational CLI.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "bootstrap-super-admin",
        help=(
            "Create the first SUPER_ADMIN if none exists. "
            "Reads INIT_SUPER_ADMIN_EMAIL + INIT_SUPER_ADMIN_PASSWORD from the env."
        ),
    )

    args = parser.parse_args(argv)
    if args.command == "bootstrap-super-admin":
        return asyncio.run(_run_bootstrap_super_admin())

    parser.error(f"unknown command: {args.command}")
    return 2  # unreachable; argparse exits


if __name__ == "__main__":
    raise SystemExit(main())
