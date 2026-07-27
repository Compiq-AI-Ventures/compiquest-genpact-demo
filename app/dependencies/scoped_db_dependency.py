"""Tenant-scoped database session dependency.

Use :func:`get_tenant_scoped_db` on every business endpoint that
touches RLS-protected tables. It:

1. Resolves the active tenant via :func:`get_active_tenant_id`
   (header / JWT / single-membership fallback).
2. Sets the Postgres session GUC ``app.current_tenant`` to that
   tenant's UUID. RLS policies on business tables compare against
   this GUC.
3. Yields the session.

On cleanup, :func:`get_db` runs ``RESET ALL`` so the connection
returns to the pool without leaking GUCs to the next request.

Defense-in-depth model
----------------------
Repositories ALSO filter explicitly by ``tenant_id`` (e.g.
``WHERE tenant_id = :active_tenant_id``). RLS is the safety net: if
the application code ever forgets the WHERE clause, the database
still hides cross-tenant rows. Belt + suspenders.

Platform-admin override
-----------------------
For cross-tenant admin queries (e.g. SUPER_ADMIN listing every
tenant's department count for ops), use
:func:`get_unrestricted_db` instead. It sets
``app.platform_override = 'true'`` so RLS policies allow access to
every tenant's rows.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import RoleCode
from app.dependencies.db_dependency import get_db
from app.dependencies.role_dependency import require_platform_roles
from app.dependencies.tenant_dependency import (
    TenantContextRequiredError,
    get_active_tenant_id,
)
from app.models.user import User


async def get_tenant_scoped_db(
    db: AsyncSession = Depends(get_db),
    active_tenant_id: uuid.UUID | None = Depends(get_active_tenant_id),
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a session with ``app.current_tenant`` set to the active
    tenant. Raises :class:`TenantContextRequiredError` (400) when no
    tenant could be resolved — every caller of this dep operates
    inside *some* tenant.
    """
    if active_tenant_id is None:
        raise TenantContextRequiredError()

    # ``SET <name> = <value>`` is a parser-level statement: Postgres
    # does NOT accept bind parameters here, and asyncpg's prepared
    # statement compilation would produce ``SET app.current_tenant = $1``
    # — rejected as a syntax error. Use the ``set_config(name, value,
    # is_local)`` built-in instead: it's a regular function call so it
    # accepts parameters, and ``is_local=false`` makes it
    # session-scoped (the same semantics ``SET`` would have given us).
    # ``RESET ALL`` in get_db's cleanup keeps the connection-pool
    # state hygienic.
    await db.execute(
        text("SELECT set_config('app.current_tenant', :tid, false)"),
        {"tid": str(active_tenant_id)},
    )
    yield db


async def get_unrestricted_db(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(
        require_platform_roles([RoleCode.SUPER_ADMIN, RoleCode.PLATFORM_ADMIN])
    ),
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a session that BYPASSES tenant RLS.

    Reserved for cross-tenant admin queries by platform admins. Logged
    explicitly via the audit trail at the route layer where needed.
    """
    await db.execute(text("SET app.platform_override = 'true'"))
    yield db
