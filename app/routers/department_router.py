"""Department CRUD — the first tenant-scoped business module.

Route conventions established here that future business modules
should follow:

* No tenant id in the URL path. The tenant is resolved from
  ``X-Tenant-ID`` / JWT / single-membership fallback (the existing
  ``get_active_tenant_id`` machinery).
* Sessions come from :func:`get_tenant_scoped_db`, which sets the
  ``app.current_tenant`` GUC so the RLS policy on the table fires.
* Repositories also filter by ``tenant_id`` explicitly — defense
  in depth.
* Read endpoints accept any tenant member; writes require
  ``TENANT_ADMIN`` or ``HR``.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import RoleCode
from app.dependencies.role_dependency import require_tenant_roles
from app.dependencies.scoped_db_dependency import get_tenant_scoped_db
from app.dependencies.tenant_dependency import (
    TenantContext,
    get_tenant_context,
)
from app.repositories import department_repository
from app.schemas.department_schema import (
    DepartmentCreateRequest,
    DepartmentResponse,
    DepartmentUpdateRequest,
)
from app.services import department_service
from app.utils.response_builder import success_response

router = APIRouter(prefix="/departments", tags=["departments"])

# Reads: any tenant member.
_READ_DEPENDENCY = get_tenant_context

# Writes: TENANT_ADMIN or HR within the active tenant.
_WRITE_DEPENDENCY = require_tenant_roles([RoleCode.TENANT_ADMIN, RoleCode.HR])


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
@router.get(
    "",
    summary="List departments in the active tenant",
    responses={
        400: {"description": "Tenant context required"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller is not a member of the active tenant"},
    },
)
async def list_departments(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(_READ_DEPENDENCY),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    rows, total = await department_repository.list_for_tenant(
        db, ctx.active_tenant_id, limit=limit, offset=offset
    )
    return success_response(
        message="Departments",
        data={
            "items": [DepartmentResponse.model_validate(d) for d in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        },
    )


# ---------------------------------------------------------------------------
# Get one
# ---------------------------------------------------------------------------
@router.get(
    "/{department_id}",
    summary="Get a department",
    responses={
        400: {"description": "Tenant context required"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller is not a member of the active tenant"},
        404: {"description": "Department not found in this tenant"},
    },
)
async def get_department(
    department_id: uuid.UUID,
    ctx: TenantContext = Depends(_READ_DEPENDENCY),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    dept = await department_repository.get_for_tenant(
        db, ctx.active_tenant_id, department_id
    )
    if dept is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Department not found.",
        )
    return success_response(
        message="Department",
        data=DepartmentResponse.model_validate(dept),
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a department in the active tenant",
    responses={
        400: {"description": "Duplicate code or missing tenant context"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller lacks the required role"},
        422: {"description": "Schema validation error"},
    },
)
async def create_department(
    body: DepartmentCreateRequest,
    ctx: TenantContext = Depends(_WRITE_DEPENDENCY),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    dept = await department_service.create_department(
        db,
        tenant_id=ctx.active_tenant_id,
        code=body.code,
        name=body.name,
        description=body.description,
    )
    return success_response(
        message="Department created successfully",
        data=DepartmentResponse.model_validate(dept),
    )


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------
@router.patch(
    "/{department_id}",
    summary="Update a department's name / description",
    responses={
        400: {"description": "Missing tenant context"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller lacks the required role"},
        404: {"description": "Department not found in this tenant"},
        422: {"description": "Schema validation error"},
    },
)
async def update_department(
    department_id: uuid.UUID,
    body: DepartmentUpdateRequest,
    ctx: TenantContext = Depends(_WRITE_DEPENDENCY),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict[str, Any]:
    dept = await department_repository.get_for_tenant(
        db, ctx.active_tenant_id, department_id
    )
    if dept is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Department not found.",
        )
    updated = await department_service.update_department(
        db, dept, name=body.name, description=body.description
    )
    return success_response(
        message="Department updated",
        data=DepartmentResponse.model_validate(updated),
    )


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------
@router.delete(
    "/{department_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a department",
    responses={
        400: {"description": "Missing tenant context"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller lacks the required role"},
        404: {"description": "Department not found in this tenant"},
    },
)
async def delete_department(
    department_id: uuid.UUID,
    ctx: TenantContext = Depends(_WRITE_DEPENDENCY),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> None:
    dept = await department_repository.get_for_tenant(
        db, ctx.active_tenant_id, department_id
    )
    if dept is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Department not found.",
        )
    await department_service.delete_department(db, dept)
    return None
