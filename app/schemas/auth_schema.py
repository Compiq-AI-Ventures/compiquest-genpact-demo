"""Pydantic schemas for the auth endpoints.

These describe the *wire shape* of requests and responses. They're
deliberately separate from the SQLAlchemy ``User`` model so that the
API contract can evolve independently of the database schema.

Email handling: every email field is validated for syntax (``EmailStr``)
and then normalized to lowercase. Email uniqueness is enforced at the
DB level *per tenant*, so two tenants can each own ``alice@hr.com``
without collision; platform users (no tenant) remain globally unique.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def _normalize_email(value: str) -> str:
    """Lowercase + strip the email so all lookups use a single canonical form."""
    return value.strip().lower()


def _normalize_tenant_code(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip().lower()
    return v or None


class UserLoginRequest(BaseModel):
    """Body for POST /auth/login.

    Login resolution
    ----------------
    1. Try a platform-user lookup first (``users.tenant_id IS NULL``)
       by email. If matched and the password verifies, the caller is
       a platform user.
    2. Otherwise resolve the tenant. If ``tenant_code`` is provided,
       look up that tenant directly. Else, try to resolve via the
       email's domain (``alice@acme.com`` → tenant where
       ``domain = 'acme.com'``).
    3. With a tenant in hand, look the user up there (``users``
       restricted to that ``tenant_id``).

    ``tenant_code`` is required only for tenant users whose email is
    *not* in their tenant's domain (e.g. consultants set up with their
    personal email as the bootstrap admin).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    tenant_code: str | None = Field(default=None, max_length=64)

    @field_validator("email", mode="after")
    @classmethod
    def _lowercase_email(cls, v: str) -> str:
        return _normalize_email(v)

    @field_validator("tenant_code", mode="after")
    @classmethod
    def _normalize_tenant_code(cls, v: str | None) -> str | None:
        return _normalize_tenant_code(v)


class TokenResponse(BaseModel):
    """Response body for a successful login or refresh.

    Shape mirrors the OAuth2 ``Bearer`` token convention so it plugs
    into standard clients (Swagger UI's "Authorize" button, ``httpx``
    auth helpers, etc.) without translation.

    ``expires_in`` is the access-token lifetime in seconds and matches
    the OAuth2 spec field of the same name. ``refresh_token`` is
    rotated on every ``/auth/refresh`` call and the previous one is
    revoked at the same time, so a leaked refresh token becomes
    useless the moment the legitimate client refreshes.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    """Body for POST /auth/refresh.

    A single ``refresh_token`` field — that's enough to identify the
    user (the token's ``sub`` claim), and the server re-derives roles /
    tenants from the database rather than trusting any claims the
    client might add.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    refresh_token: str = Field(min_length=1)
