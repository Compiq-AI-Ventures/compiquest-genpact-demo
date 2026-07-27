"""Department orchestration."""

from __future__ import annotations

import uuid

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DomainError
from app.models.department import Department
from app.repositories import department_repository


class DepartmentCodeAlreadyExistsError(DomainError):
    """A department with this code already exists in this tenant."""

    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "DEPARTMENT_CODE_ALREADY_EXISTS"

    def __init__(self, code: str) -> None:
        super().__init__(
            message=f"A department with code {code!r} already exists in this tenant.",
            details={"code": code},
        )


async def create_department(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    code: str,
    name: str,
    description: str | None,
) -> Department:
    # Friendly pre-check; the unique constraint catches the race.
    if await department_repository.get_by_code(db, tenant_id, code) is not None:
        raise DepartmentCodeAlreadyExistsError(code)

    dept = Department(
        tenant_id=tenant_id,
        code=code,
        name=name,
        description=description,
    )
    try:
        await department_repository.create(db, dept)
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise DepartmentCodeAlreadyExistsError(code) from exc

    return dept


async def update_department(
    db: AsyncSession,
    department: Department,
    *,
    name: str | None,
    description: str | None,
) -> Department:
    if name is not None:
        department.name = name
    if description is not None:
        department.description = description
    await db.flush()
    # ``updated_at`` is ``server_default=func.now()`` + ``onupdate=
    # func.now()``: after the UPDATE the new value lives only on the
    # server. SQLAlchemy marks the attribute expired so the next read
    # triggers a SELECT — but the router's ``DepartmentResponse
    # .model_validate(updated)`` accesses it outside an awaited
    # session call, which is fatal under async SQLAlchemy
    # (``MissingGreenlet``). Refresh now while we're still inside an
    # awaited coroutine, so the response builder reads a plain Python
    # attribute.
    await db.refresh(department, ["updated_at"])
    return department


async def delete_department(db: AsyncSession, department: Department) -> None:
    await department_repository.delete(db, department)
    await db.flush()
