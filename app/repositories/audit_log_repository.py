"""Persistence helpers for the :class:`~app.models.audit_log.AuditLog`
aggregate.

Repository functions intentionally do not commit. The
:func:`app.services.audit_log_service.log_action` service owns the
unit-of-work boundary because audit writes are committed independently
of the main API flow (see its docstring for why).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


async def create_audit_log(db: AsyncSession, audit: AuditLog) -> AuditLog:
    """Add ``audit`` to the session and flush so server defaults populate.

    The session is *not* committed here — the audit service handles
    commit so it can wrap the entire write in best-effort error
    handling.
    """
    db.add(audit)
    await db.flush()
    return audit
