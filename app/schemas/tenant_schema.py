"""Pydantic schemas for tenant-management endpoints.

Three request shapes:

* :class:`TenantCreateRequest`  — ``POST /admin/tenants``
  embeds an :class:`TenantInitialAdminRequest` so the customer can log
  in immediately. Creating a tenant without an admin is intentionally
  not allowed — a tenant nobody can administer is dead weight.
* :class:`TenantUpdateRequest`  — ``PATCH /admin/tenants/{id}``
  any subset of ``name``, ``domain``, ``status`` may be sent. Status
  transitions are enforced by the service.

And the response shapes:

* :class:`TenantResponse`         — bare tenant fields.
* :class:`TenantWithAdminResponse` — returned by ``POST`` so clients
  see both the new tenant and the bootstrap admin in one call.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.tenant import TenantStatus
from app.schemas.user_schema import UserResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# Strict-but-pragmatic domain validator:
#  * total length 1-253 chars (RFC 1035)
#  * each label 1-63 chars, lowercase letters/digits/hyphens, may not
#    start or end with a hyphen
#  * at least two labels (so we always have a TLD; pure single-label
#    intranet hostnames like "localhost" are intentionally rejected
#    because they cannot serve as an SSO/email discovery anchor)
_DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)


def _normalize_code(value: str) -> str:
    """Lowercase + validate the tenant code is URL/slug-safe."""
    v = value.strip().lower()
    if not _CODE_PATTERN.match(v):
        raise ValueError(
            "code must start with a letter or digit and contain only "
            "lowercase letters, digits, underscores, or dashes"
        )
    return v


def _normalize_domain(value: str) -> str:
    """Lowercase + strict-validate the domain.

    Required at create time; strict regex; must look like a real
    multi-label domain. We don't query DNS here — that would couple
    config to network state — but we do reject any input that
    couldn't possibly route email or anchor SSO discovery.
    """
    v = value.strip().lower()
    if not v:
        raise ValueError("domain is required")
    if not _DOMAIN_PATTERN.match(v):
        raise ValueError(
            "domain must be a valid lowercase DNS name with at least "
            "two labels (e.g. 'acme.com'); each label 1-63 chars of "
            "letters, digits or hyphens, not starting or ending with a hyphen"
        )
    return v


def _normalize_optional_domain(value: str | None) -> str | None:
    """Used by PATCH where domain is optional (omit = no change)."""
    if value is None:
        return None
    return _normalize_domain(value)


# ---------------------------------------------------------------------------
# Initial-admin sub-schema
# ---------------------------------------------------------------------------
class TenantInitialAdminRequest(BaseModel):
    """Bootstrap administrator for a newly-created tenant.

    The role is implicit — ``TENANT_ADMIN`` of the new tenant. The
    request body doesn't accept ``role_codes`` because there's exactly
    one valid choice and accepting it would only let clients break
    things.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)

    @field_validator("email", mode="after")
    @classmethod
    def _lowercase_email(cls, v: str) -> str:
        return v.strip().lower()


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
class TenantCreateRequest(BaseModel):
    """Body for ``POST /admin/tenants``.

    ``domain`` is required and globally unique. It is the canonical
    email/SSO discovery anchor: at login, an email like
    ``alice@acme.com`` is routed to the tenant whose ``domain`` is
    ``acme.com``. Two tenants cannot share a domain.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    code: str = Field(min_length=1, max_length=64)
    domain: str = Field(min_length=1, max_length=253)
    initial_admin: TenantInitialAdminRequest

    @field_validator("code", mode="after")
    @classmethod
    def _normalize_code(cls, v: str) -> str:
        return _normalize_code(v)

    @field_validator("domain", mode="after")
    @classmethod
    def _normalize_domain(cls, v: str) -> str:
        return _normalize_domain(v)


class TenantUpdateRequest(BaseModel):
    """Body for ``PATCH /admin/tenants/{tenant_id}``.

    Every field is optional; only the fields that appear in the body
    will be updated. The service rejects status transitions out of
    ``DISABLED`` (it's terminal). Updating ``domain`` re-checks
    uniqueness — if another tenant already owns the new domain, the
    request fails with ``TENANT_DOMAIN_ALREADY_EXISTS``.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    domain: str | None = Field(default=None, min_length=1, max_length=253)
    status: TenantStatus | None = None

    @field_validator("domain", mode="after")
    @classmethod
    def _normalize_domain(cls, v: str | None) -> str | None:
        return _normalize_optional_domain(v)


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------
class TenantResponse(BaseModel):
    """Bare tenant fields. No relationships, no internal columns."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    domain: str
    status: str
    created_at: datetime
    updated_at: datetime


class TenantWithAdminResponse(BaseModel):
    """Returned by ``POST /admin/tenants`` so the client gets both
    objects without a follow-up GET."""

    tenant: TenantResponse
    admin: UserResponse
