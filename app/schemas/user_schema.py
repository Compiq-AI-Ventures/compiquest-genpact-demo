"""Pydantic response schemas for User-facing endpoints.

These are deliberately separate from :mod:`app.schemas.auth_schema` so
that request shapes (which include sensitive inputs like ``password``)
and response shapes (which must never leak ``password_hash``) cannot
be confused at the type level.

Two response models live here:

* :class:`UserResponse` — the basic profile shape returned by the
  admin user-creation endpoints. Includes a flat ``roles`` list of
  every role code the user holds.
* :class:`CurrentUserResponse` — the shape returned by ``/auth/me``.
  Single-tenant model: every user has exactly one tenant binding (or
  none, for platform users), so the response carries one ``tenant``
  object instead of a list, and a flat ``roles`` list instead of a
  per-tenant map.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

from app.core.authorization import RoleProfile
from app.models.user import User


class UserResponse(BaseModel):
    """Public-safe representation of a user.

    Note the absence of ``password_hash`` — Pydantic serializes only
    the fields declared here, so even if the caller passes a full
    ``User`` ORM instance, the hash never reaches the wire.

    ``roles`` is a list of role *codes* (strings). The wire format
    never exposes role IDs or any other internal columns.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID | None = None
    """The tenant this user belongs to. ``None`` for platform users."""

    email: EmailStr
    first_name: str
    last_name: str | None = None
    is_active: bool
    created_at: datetime
    roles: list[str]

    @classmethod
    def from_user(cls, user: User) -> UserResponse:
        """Build a response from a :class:`User`, projecting roles to codes."""
        return cls(
            id=user.id,
            tenant_id=user.tenant_id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            is_active=user.is_active,
            created_at=user.created_at,
            roles=[r.code for r in user.roles],
        )


class TenantSummary(BaseModel):
    """Compact tenant view for embedding in /auth/me responses.

    Internal columns (timestamps, status detail) are omitted — the
    full picture lives at the tenant-management endpoints.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    domain: str
    status: str


class CurrentUserResponse(BaseModel):
    """Response for ``GET /auth/me``.

    Single-tenant model: a user belongs to exactly one tenant or to no
    tenant (platform user). The response reflects that:

    * Platform user → ``tenant`` is ``None``, ``roles`` lists the
      PLATFORM-scope codes the user holds.
    * Tenant user → ``tenant`` is the user's tenant summary, ``roles``
      lists the TENANT-scope codes held inside it.

    There is no per-tenant role map and no tenant list — both
    constructs were artefacts of the old multi-tenancy-per-user model.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    first_name: str
    last_name: str | None = None
    is_active: bool
    created_at: datetime

    tenant: TenantSummary | None
    """The user's tenant, or ``None`` for platform users."""

    roles: list[str]
    """Role codes the user holds. PLATFORM-scope iff ``tenant is None``,
    TENANT-scope otherwise."""
    job_title: str | None = None
    department: str | None = None

    @classmethod
    def build(
        cls,
        user: User,
        *,
        profile: RoleProfile,
    ) -> CurrentUserResponse:
        if user.tenant_id is None or user.tenant is None:
            tenant_summary = None
            roles = sorted(profile.platform_roles)
        else:
            tenant_summary = TenantSummary.model_validate(user.tenant)
            roles = sorted(profile.tenant_roles)

        return cls(
            id=user.id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            is_active=user.is_active,
            created_at=user.created_at,
            tenant=tenant_summary,
            roles=roles,
            job_title=user.job_title if hasattr(user, "job_title") else None,
            department=user.department.name if hasattr(user, "department") and user.department else None,
        )
