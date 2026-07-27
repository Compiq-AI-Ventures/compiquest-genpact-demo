"""Best-effort audit logging.

Two entry points, picked based on whether the action succeeded or
failed:

* :func:`log_action` — writes one row inside the **caller's**
  transaction (no commit). Use this for success paths so the audit row
  and the action it records either both land or both roll back. This
  is the right call from a route handler that returns successfully:
  the request-scoped Unit of Work (``get_db``) commits once at the
  end, and the audit row goes with it.

* :func:`log_action_independent` — opens its OWN short-lived session
  and commits it. Use this for failure paths (e.g. failed login) where
  the caller's session is going to be rolled back but the audit row
  must still survive. This is the right call from inside the
  ``raise`` arm of an exception path.

Both are best-effort — neither raises. If the database is unreachable
or the row is invalid, structlog records ``audit_log_failed`` and the
caller continues.

What NOT to log
---------------
Never put any of the following into ``metadata``:

* Plaintext passwords or password hashes
* JWT tokens, refresh tokens, API keys
* Full request bodies or response bodies (might contain PII)
* Bank/card numbers, government IDs
* Health information

If in doubt, leave it out — audit rows are forever, and a leak from
this table is the worst kind because it's a centralized timeline.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.repositories import audit_log_repository

_log = structlog.get_logger(__name__)

# Session factory used by :func:`log_action_independent`. Defaults to
# the production ``AsyncSessionLocal`` (bound to ``settings.database_url``);
# tests override it via :func:`set_independent_session_factory` so the
# audit row lands on the test engine instead of the production one.
_independent_session_factory: Callable[[], AsyncSession] = AsyncSessionLocal


def set_independent_session_factory(
    factory: async_sessionmaker[AsyncSession] | Callable[[], AsyncSession] | None,
) -> None:
    """Override (or reset) the factory used for independent audit
    sessions. Test helper.

    Pass ``None`` to restore the production default.
    """
    global _independent_session_factory
    _independent_session_factory = factory if factory is not None else AsyncSessionLocal


def _build(
    *,
    actor_user_id: uuid.UUID | None,
    action: str,
    tenant_id: uuid.UUID | None,
    resource_type: str | None,
    resource_id: str | None,
    request_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
    metadata: dict[str, Any] | None,
) -> AuditLog:
    return AuditLog(
        actor_user_id=actor_user_id,
        tenant_id=tenant_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra_data=metadata,
    )


async def log_action(
    db: AsyncSession,
    actor_user_id: uuid.UUID | None,
    action: str,
    tenant_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Write one audit row inside the caller's transaction. Never raises.

    Flushes the row so server-generated columns are populated, but does
    NOT commit — the surrounding request's Unit of Work does that. If
    the surrounding transaction rolls back, this audit row goes with
    it (which is what you want for a success path that fails late).

    Args:
        db: SQLAlchemy session — the same one the caller is using to
            perform the action being audited.
        actor_user_id: The user who took the action; ``None`` for
            unauthenticated callers.
        action: Short, stable action code (e.g. ``"USER_CREATED"``).
        tenant_id: Tenant the action belongs to. ``None`` for
            platform-level actions. Tenant-scoped actions MUST set this.
        resource_type: Type of the entity acted upon (``"user"``,
            ``"role"``, ...). Optional.
        resource_id: Identifier of the entity acted upon, as a string.
            Optional.
        request_id: Correlation ID from ``X-Request-ID`` middleware.
        ip_address: Client IP from the request.
        user_agent: ``User-Agent`` header.
        metadata: Action-specific JSON-serializable context. **Read the
            module docstring for what NOT to put here.**
    """
    try:
        audit = _build(
            actor_user_id=actor_user_id,
            action=action,
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata=metadata,
        )
        await audit_log_repository.create_audit_log(db, audit)
        # Flush so the row is materialized inside the active tx. The
        # request's Unit of Work commits this for us.
        await db.flush()
    except Exception:
        _log.exception(
            "audit_log_failed",
            action=action,
            actor_user_id=str(actor_user_id) if actor_user_id else None,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        # Don't rollback — that would discard the caller's other work.
        # We're best-effort: surface the failure in logs and move on.


async def log_action_independent(
    actor_user_id: uuid.UUID | None,
    action: str,
    tenant_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Write one audit row in its own committed transaction. Never raises.

    Opens a fresh ``AsyncSession``, writes the row, commits, and
    closes — independent of any session the caller might hold. Use
    this from the failure arm of an exception path so the audit row
    survives even when the caller's transaction is rolled back.

    Note that this opens a NEW database connection from the pool; it
    sets no GUCs, so RLS policies see no ``app.current_tenant``. That's
    fine for ``audit_logs`` (no RLS policy on it today), but if RLS is
    ever extended to this table, callers will need to set the GUC
    themselves before invoking this function.
    """
    try:
        async with _independent_session_factory() as session:
            audit = _build(
                actor_user_id=actor_user_id,
                action=action,
                tenant_id=tenant_id,
                resource_type=resource_type,
                resource_id=resource_id,
                request_id=request_id,
                ip_address=ip_address,
                user_agent=user_agent,
                metadata=metadata,
            )
            await audit_log_repository.create_audit_log(session, audit)
            await session.commit()
    except Exception:
        _log.exception(
            "audit_log_independent_failed",
            action=action,
            actor_user_id=str(actor_user_id) if actor_user_id else None,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        # Best-effort: nothing more to do — the session context manager
        # has already cleaned up. Suppress to honour the no-raise contract.
        with contextlib.suppress(Exception):
            pass
