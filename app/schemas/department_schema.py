"""Pydantic schemas for the department endpoints."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]*$")


def _normalize_code(value: str) -> str:
    """Uppercase + validate the department code is short-string-ID-shaped."""
    v = value.strip().upper()
    if not _CODE_PATTERN.match(v):
        raise ValueError(
            "code must be uppercase letters, digits, dashes or underscores"
            " and start with a letter or digit"
        )
    return v


class DepartmentCreateRequest(BaseModel):
    """Body for ``POST /departments``."""

    model_config = ConfigDict(str_strip_whitespace=True)

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)

    @field_validator("code", mode="after")
    @classmethod
    def _normalize_code(cls, v: str) -> str:
        return _normalize_code(v)


class DepartmentUpdateRequest(BaseModel):
    """Body for ``PATCH /departments/{id}``. Code is intentionally
    immutable; renaming is `name` only."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)


class DepartmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    code: str
    name: str
    description: str | None = None
    created_at: datetime
    updated_at: datetime
