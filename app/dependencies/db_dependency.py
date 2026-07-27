"""Request-scoped database session dependency for FastAPI routes.

Unit-of-Work contract
---------------------
``get_db`` is a *unit of work* per HTTP request:

* Services and repositories ``flush()`` — they MUST NOT ``commit()``.
* If the route handler returns normally, ``get_db`` commits once.
* If the route raises, ``get_db`` rolls back.

This makes it impossible to half-commit a request: an action plus its
audit row either both land or both vanish. Callers that genuinely need
their own transaction (e.g. ``audit_log_service.log_action_independent``
recording a failure on a session that's being rolled back) open their
OWN session via ``AsyncSessionLocal`` rather than reusing the request
session.
"""

import contextlib
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a per-request ``AsyncSession`` and commit on success.

    The session is committed once when the route handler returns
    successfully, rolled back if the route raises, and always closed
    when the request finishes.

    On cleanup we run ``RESET ALL`` so the underlying connection
    returns to the pool with a clean GUC slate. Without this, the
    ``app.current_tenant`` / ``app.platform_override`` settings
    plumbed in by :mod:`app.dependencies.scoped_db_dependency` could
    leak into a subsequent request that picks up the same connection.

    Usage::

        from fastapi import Depends
        from sqlalchemy.ext.asyncio import AsyncSession
        from app.dependencies.db_dependency import get_db

        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            ...

    Services called from the route should ``flush()`` to materialize
    server-generated columns (ids, defaults) but never ``commit()``.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            # Route returned normally — commit the unit of work. If the
            # commit itself fails, propagate so the client gets a 500
            # instead of a misleading 200.
            await session.commit()
        finally:
            # Best-effort cleanup of any RLS GUCs set during this
            # request. ``RESET ALL`` is a no-op when nothing was set.
            # Run after commit/rollback so it runs on a clean tx.
            with contextlib.suppress(Exception):
                await session.execute(text("RESET ALL"))
                await session.commit()
