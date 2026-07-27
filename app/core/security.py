"""Security primitives: password hashing/verification and JWT signing."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import jwt
from passlib.context import CryptContext

from app.core.config import get_settings

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
# A CryptContext is passlib's main entrypoint. We register a single scheme
# (bcrypt). ``deprecated="auto"`` means: if more schemes are added later,
# any hash produced by an older scheme is treated as "needs rehash" so we
# can transparently upgrade users' stored hashes on their next successful
# login (via ``CryptContext.verify_and_update``).
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    """Return a bcrypt hash of ``plain_password``.

    bcrypt generates a per-password salt automatically and embeds it in the
    output, so the caller never needs to manage salts. Output is a single
    string roughly 60 characters long that contains the algorithm
    identifier, cost factor, salt, and digest — store it in
    ``users.password_hash`` as-is.

    Note: bcrypt silently truncates inputs beyond 72 bytes. The
    admin-user creation schemas cap password length at 128 chars so
    callers fail validation before reaching this function.
    """
    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Return ``True`` iff ``plain_password`` matches ``hashed_password``.

    Comparison is performed in constant time relative to the hash length, so
    timing differences cannot leak how close a guess was to the real
    password. Returns ``False`` (rather than raising) for malformed hashes
    so authentication paths can treat any failure as "wrong credentials".
    """
    try:
        return _pwd_context.verify(plain_password, hashed_password)
    except ValueError:
        # Raised by passlib when the stored hash isn't a recognized format.
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
def create_access_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None,
) -> str:
    """Sign a JWT access token containing the given claims.

    Adds standard ``iat`` (issued at) and ``exp`` (expiration) claims using
    timezone-aware UTC datetimes. ``python-jose`` serializes those to Unix
    timestamps as required by RFC 7519.

    Args:
        data: Custom claims to embed (e.g. ``{"sub": user_id, "role": ...}``).
              Caller should set ``sub`` to the user identifier.
        expires_delta: Optional override for the access token lifetime.
                       Defaults to ``settings.access_token_expire_minutes``.

    Returns:
        The encoded JWT as a string.
    """
    settings = get_settings()

    now = datetime.now(UTC)
    expire = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.access_token_expire_minutes)
    )

    # ``jti`` (JWT ID) is a unique identifier for this token. It's the
    # key we use to revoke specific tokens via the deny-list (logout,
    # password change, role change, suspected compromise).
    to_encode: dict[str, Any] = {
        **data,
        "iat": now,
        "exp": expire,
        "jti": uuid.uuid4().hex,
        "type": "access",
    }
    return jwt.encode(
        to_encode,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def create_refresh_token(
    *,
    user_id: str,
    expires_delta: timedelta | None = None,
) -> str:
    """Sign a refresh token bound to ``user_id``.

    Refresh tokens are deliberately minimal: ``sub`` (user id), a fresh
    ``jti``, ``type="refresh"`` so the access-token dependency can
    refuse to authenticate with one, and an ``exp`` that's longer than
    the access token's. They carry no role / tenant claims — those are
    re-derived from the DB when ``/auth/refresh`` mints the next access
    token, so revoking a role takes effect on the next refresh instead
    of being baked into a long-lived token.

    Lifetime defaults to ``settings.refresh_token_expire_days``.
    """
    settings = get_settings()

    now = datetime.now(UTC)
    expire = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(days=settings.refresh_token_expire_days)
    )

    to_encode: dict[str, Any] = {
        "sub": user_id,
        "iat": now,
        "exp": expire,
        "jti": uuid.uuid4().hex,
        "type": "refresh",
    }
    return jwt.encode(
        to_encode,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
