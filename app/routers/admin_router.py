"""Admin-driven user provisioning.

Two endpoints, mirroring the platform/tenant role split:

* ``POST /admin/users`` — creates a platform user. Auth:
  ``SUPER_ADMIN`` or ``PLATFORM_ADMIN`` at the platform level.
* ``POST /admin/tenants/{tenant_id}/users`` — creates a user inside a
  tenant. Auth: ``TENANT_ADMIN`` of that tenant *or* a platform admin
  override.

This is the only path through which user accounts get created.
Public self-registration was removed deliberately — CompIQCoreBe is
an enterprise SaaS where account provisioning belongs in admin hands.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import RoleCode
from app.dependencies.admin_dependency import AdminContext, require_admin_for_tenant
from app.dependencies.db_dependency import get_db
from app.dependencies.role_dependency import require_platform_roles
from app.models.user import User
from app.schemas.admin_user_schema import (
    AdminCreatePlatformUserRequest,
    AdminCreateTenantUserRequest,
)
from app.schemas.user_schema import UserResponse
from app.services import admin_user_service
from app.utils.response_builder import success_response

router = APIRouter(prefix="/admin", tags=["admin"])


def _request_context(request: Request) -> dict[str, str | None]:
    return {
        "request_id": getattr(request.state, "request_id", None),
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


@router.post(
    "/users",
    status_code=status.HTTP_201_CREATED,
    summary="Create a platform user",
    responses={
        400: {"description": "Validation / scope mismatch / duplicate email"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller is not a platform admin"},
        422: {"description": "Schema validation error"},
    },
)
async def create_platform_user_endpoint(
    request: Request,
    body: AdminCreatePlatformUserRequest,
    actor: User = Depends(
        require_platform_roles([RoleCode.SUPER_ADMIN, RoleCode.PLATFORM_ADMIN])
    ),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Provision a new platform user.

    ``role_codes`` must reference PLATFORM-scope roles only.
    """
    user = await admin_user_service.create_platform_user(
        db,
        body,
        actor_user_id=actor.id,
        **_request_context(request),
    )
    return success_response(
        message="User created successfully",
        data=UserResponse.from_user(user),
    )


@router.post(
    "/tenants/{tenant_id}/users",
    status_code=status.HTTP_201_CREATED,
    summary="Create a tenant user",
    responses={
        400: {"description": "Validation / scope mismatch / duplicate email"},
        401: {"description": "Missing or invalid Bearer token"},
        403: {"description": "Caller cannot administer this tenant"},
        404: {"description": "Tenant not found"},
        422: {"description": "Schema validation error"},
    },
)
async def create_tenant_user_endpoint(
    request: Request,
    tenant_id: uuid.UUID,
    body: AdminCreateTenantUserRequest,
    admin: AdminContext = Depends(require_admin_for_tenant),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Provision a new user inside ``tenant_id``.

    ``role_codes`` must reference TENANT-scope roles only. The user
    is bound to the tenant via ``users.tenant_id`` and granted each
    role.
    """
    user = await admin_user_service.create_tenant_user(
        db,
        admin.tenant,
        body,
        actor_user_id=admin.user.id,
        **_request_context(request),
    )
    return success_response(
        message="Tenant user created successfully",
        data=UserResponse.from_user(user),
    )
