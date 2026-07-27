"""Tenant lifecycle endpoints.

All routes here are gated to platform admins (``SUPER_ADMIN`` or
``PLATFORM_ADMIN``). Tenant admins of an individual tenant administer
*inside* the tenant via ``/admin/tenants/{tenant_id}/users``; only
platform staff create / list / suspend tenants themselves.

Endpoints
---------

* ``POST   /admin/tenants``               — create + bootstrap admin.
* ``GET    /admin/tenants``               — paginated list, optional
                                            ``?status=`` filter.
* ``GET    /admin/tenants/{tenant_id}``   — single tenant.
* ``PATCH  /admin/tenants/{tenant_id}``   — update name/domain/status.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import RoleCode
from app.dependencies.db_dependency import get_db
from app.dependencies.role_dependency import require_platform_roles
from app.models.tenant import TenantStatus
from app.models.user import User
from app.repositories import tenant_repository
from app.schemas.tenant_schema import (
    TenantCreateRequest,
    TenantResponse,
    TenantUpdateRequest,
    TenantWithAdminResponse,
)
from app.schemas.user_schema import UserResponse
from app.services import tenant_service
from app.utils.response_builder import success_response

router = APIRouter(prefix="/admin/tenants", tags=["admin.tenants"])

_PLATFORM_ADMIN_DEPENDENCY = require_platform_roles(
    [RoleCode.SUPER_ADMIN, RoleCode.PLATFORM_ADMIN]
)
# Read-only ops also accept SUPPORT_ADMIN so customer-support staff
# can investigate without write access.
_PLATFORM_READ_DEPENDENCY = require_platform_roles(
    [RoleCode.SUPER_ADMIN, RoleCode.PLATFORM_ADMIN, RoleCode.SUPPORT_ADMIN]
)


def _request_context(request: Request) -> dict[str, str | None]:
    return {
        "request_id": getattr(request.state, "request_id", None),
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a tenant + bootstrap TENANT_ADMIN",
    responses={
        400: {"description": "Duplicate code, duplicate admin email, or bad input"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller is not a platform admin"},
        422: {"description": "Schema validation error"},
    },
)
async def create_tenant(
    request: Request,
    body: TenantCreateRequest,
    actor: User = Depends(_PLATFORM_ADMIN_DEPENDENCY),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a new tenant and its bootstrap ``TENANT_ADMIN``."""
    tenant, admin = await tenant_service.create_tenant_with_admin(
        db,
        body,
        actor_user_id=actor.id,
        **_request_context(request),
    )
    return success_response(
        message="Tenant created successfully",
        data=TenantWithAdminResponse(
            tenant=TenantResponse.model_validate(tenant),
            admin=UserResponse.from_user(admin),
        ),
    )


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
@router.get(
    "",
    summary="List tenants",
    responses={
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller is not a platform admin / support admin"},
    },
)
async def list_tenants(
    tenant_status: TenantStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _actor: User = Depends(_PLATFORM_READ_DEPENDENCY),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return a page of tenants, optionally filtered by status."""
    rows, total = await tenant_service.list_tenants(
        db,
        status=tenant_status.value if tenant_status is not None else None,
        limit=limit,
        offset=offset,
    )
    return success_response(
        message="Tenants",
        data={
            "items": [TenantResponse.model_validate(t) for t in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        },
    )


# ---------------------------------------------------------------------------
# Get one
# ---------------------------------------------------------------------------
@router.get(
    "/{tenant_id}",
    summary="Get a tenant",
    responses={
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller is not a platform admin / support admin"},
        404: {"description": "Tenant not found"},
    },
)
async def get_tenant(
    tenant_id: uuid.UUID,
    _actor: User = Depends(_PLATFORM_READ_DEPENDENCY),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    tenant = await tenant_repository.get_by_id(db, tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found.",
        )
    return success_response(
        message="Tenant",
        data=TenantResponse.model_validate(tenant),
    )


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------
@router.patch(
    "/{tenant_id}",
    summary="Update a tenant",
    responses={
        400: {"description": "Invalid status transition"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller is not a platform admin"},
        404: {"description": "Tenant not found"},
        422: {"description": "Schema validation error"},
    },
)
async def update_tenant(
    request: Request,
    tenant_id: uuid.UUID,
    body: TenantUpdateRequest,
    actor: User = Depends(_PLATFORM_ADMIN_DEPENDENCY),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    tenant = await tenant_repository.get_by_id(db, tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found.",
        )
    updated = await tenant_service.update_tenant(
        db,
        tenant,
        body,
        actor_user_id=actor.id,
        **_request_context(request),
    )
    return success_response(
        message="Tenant updated",
        data=TenantResponse.model_validate(updated),
    )
