"""Department ORM model — the first tenant-scoped business entity.

Departments are organizational units inside a tenant (Engineering,
Sales, HR, ...). Every row carries ``tenant_id`` and the table has
Postgres Row-Level Security enabled — the database itself enforces
that one tenant cannot see another's rows.

The application also filters explicitly by ``tenant_id`` in
repositories. Two layers, fail-closed.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Department(Base):
    """An organizational unit inside a tenant."""

    __tablename__ = "departments"
    __table_args__ = (
        # ``code`` must be unique *within a tenant*, not globally —
        # different tenants can both have an "ENG" department.
        UniqueConstraint(
            "tenant_id", "code", name="uq_departments_tenant_code"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Department code={self.code!r} tenant_id={self.tenant_id}>"
