"""Pydantic schemas for admin-driven user provisioning.

User accounts in CompIQCoreBe are NEVER created via public
self-registration. Two admin endpoints handle creation:

* ``POST /admin/users``                     — for platform users.
* ``POST /admin/tenants/{tenant_id}/users`` — for tenant users.

The two request shapes mirror that split: the platform shape only
accepts PLATFORM-scope role codes; the tenant shape only accepts
TENANT-scope codes. The service layer enforces the scope match — the
schema can't reach the DB.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def _normalize_email(value: str) -> str:
    return value.strip().lower()


class _AdminCreateUserBase(BaseModel):
    """Common fields for admin user-creation bodies."""

    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    role_codes: list[str] = Field(min_length=1, max_length=10)

    @field_validator("email", mode="after")
    @classmethod
    def _lowercase_email(cls, v: str) -> str:
        return _normalize_email(v)

    @field_validator("role_codes", mode="after")
    @classmethod
    def _strip_codes(cls, v: list[str]) -> list[str]:
        # Strip + de-duplicate; keep insertion order.
        seen: list[str] = []
        for code in v:
            stripped = code.strip()
            if not stripped:
                continue
            if stripped not in seen:
                seen.append(stripped)
        if not seen:
            raise ValueError("role_codes must contain at least one non-empty code")
        return seen


class AdminCreatePlatformUserRequest(_AdminCreateUserBase):
    """Body for ``POST /admin/users`` — provisions a platform user.

    All ``role_codes`` must reference roles whose ``scope = 'PLATFORM'``;
    the service layer rejects tenant-scoped codes with 400.
    """


class AdminCreateTenantUserRequest(_AdminCreateUserBase):
    """Body for ``POST /admin/tenants/{tenant_id}/users``.

    The target tenant comes from the URL path. All ``role_codes`` must
    reference roles whose ``scope = 'TENANT'``; the service layer
    rejects platform-scoped codes with 400.
    """
