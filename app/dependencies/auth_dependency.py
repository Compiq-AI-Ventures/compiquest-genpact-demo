"""Authentication dependency: resolve the current user from a Bearer JWT.

Use as ``current_user: User = Depends(get_current_user)`` on any route
that requires an authenticated caller. The dependency:

1. Reads ``Authorization: Bearer <token>`` from the request.
2. Decodes/verifies the JWT (signature, expiry).
3. Extracts the user id from the ``sub`` claim.
4. Loads the user from the database.
5. Rejects inactive accounts with 403; everything else with 401.

Returns the SQLAlchemy ``User`` instance so route handlers have full
access to the persisted record (and can avoid re-querying).
"""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.token_denylist import get_denylist
from app.dependencies.db_dependency import get_db
from app.models.user import User
from app.repositories import user_repository

# ``auto_error=False`` so we can return *401* (correct) for missing tokens
# instead of FastAPI's default *403* when ``auto_error=True``.
# ``bearerFormat="JWT"`` is purely a hint that surfaces in OpenAPI / Swagger.
_bearer_scheme = HTTPBearer(auto_error=False, bearerFormat="JWT")

# Single, stylized 401 used for every "couldn't authenticate" case so the
# response shape and headers are consistent regardless of why we failed.
# Note: deliberately vague — never reveal *why* (signature bad? expired?
# user deleted?) since that hands attackers a discrimination oracle.
_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials.",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve and return the user identified by the Bearer JWT.

    On success, the decoded JWT payload is also stashed on
    ``request.state.jwt_claims`` so other dependencies in the same
    request (notably ``get_active_tenant_id``) can read claims like
    ``active_tenant_id`` without re-decoding the token.
    """
    # 1. Header present?
    if credentials is None:
        raise _INVALID_CREDENTIALS

    settings = get_settings()

    # 2. Decode + verify (signature, exp, etc.). python-jose raises
    #    JWTError (or a subclass like ExpiredSignatureError) on any
    #    failure — we collapse them all into the same 401.
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        raise _INVALID_CREDENTIALS from exc

    # 2a. Reject access tokens that have been explicitly revoked.
    #     The deny-list is keyed by ``jti`` (the per-token id minted
    #     in ``create_access_token``); see app/core/token_denylist.py.
    #     Refresh tokens carry ``"type": "refresh"`` and route through
    #     a separate handler; if one ever reaches here, treat it as
    #     invalid — only access tokens authenticate API requests.
    if payload.get("type") == "refresh":
        raise _INVALID_CREDENTIALS

    jti = payload.get("jti")
    if isinstance(jti, str) and await get_denylist().is_revoked(jti):
        raise _INVALID_CREDENTIALS

    # 3. Extract the subject claim and parse it as a UUID.
    user_id_raw = payload.get("sub")
    if not isinstance(user_id_raw, str):
        raise _INVALID_CREDENTIALS
    try:
        user_id = uuid.UUID(user_id_raw)
    except ValueError as exc:
        raise _INVALID_CREDENTIALS from exc

    # 4. Fetch the user. If the row was deleted after the token was issued
    #    we treat it as an invalid token rather than a 404 — the client
    #    needs to log in again either way.
    user = await user_repository.get_user_by_id(db, user_id)
    if user is None:
        raise _INVALID_CREDENTIALS

    # 5. Block deactivated accounts. 403 here (not 401) because the token
    #    itself is valid — the account is just disabled.
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive.",
        )

    # Expose the verified claims to other dependencies in this request.
    request.state.jwt_claims = payload
    return user
