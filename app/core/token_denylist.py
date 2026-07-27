"""JWT deny-list backed by Redis (with an in-memory fallback).

Why a deny-list and not stateful sessions?
-------------------------------------------
JWTs are self-contained: the server can verify them without a session
table. The downside is that "log out" used to mean "client throws away
the token" — useless against a stolen token. A deny-list closes that
gap: when a token is revoked we record its ``jti`` claim with a TTL
equal to the token's remaining lifetime, and every authenticated
request checks the list before trusting the token.

We key by ``jti`` (not the raw token) because:

* It's much shorter — cheaper to store and to compare.
* Logging the ``jti`` for debugging never leaks the token itself.
* The token's signature is already verified on the inbound path; the
  ``jti`` is bound to that exact token by the signature.

Backends
--------
* **Redis** (``REDIS_URL`` is set): ``SET <prefix><jti> 1 EX <ttl>``.
  TTL = remaining seconds until the token would have expired anyway,
  so revoked entries don't grow without bound.
* **In-memory** (``REDIS_URL`` unset): a process-local dict keyed by
  ``jti``, with a tiny background sweeper that drops expired entries
  on access. **Single-instance only** — multi-replica deployments
  MUST configure Redis or one replica's logout won't apply to traffic
  routed to a sibling.

Both backends present the same async interface. The route layer never
needs to care which one is wired up.
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol

import structlog

from app.core.config import get_settings

_log = structlog.get_logger(__name__)

# All deny-list keys live under this prefix to make it trivial to
# inspect or wipe in Redis.
_REDIS_KEY_PREFIX = "jwt:denylist:"


class TokenDenylist(Protocol):
    """Async interface every backend implements."""

    async def revoke(self, jti: str, ttl_seconds: int) -> None: ...

    async def is_revoked(self, jti: str) -> bool: ...


# ---------------------------------------------------------------------------
# In-memory backend
# ---------------------------------------------------------------------------
class _InMemoryDenylist:
    """Process-local dict keyed by jti, with lazy expiry on access.

    Not thread- or process-safe across replicas. Adequate for local
    development and tests; production uses Redis.
    """

    def __init__(self) -> None:
        # ``jti -> unix_expiry``
        self._entries: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def revoke(self, jti: str, ttl_seconds: int) -> None:
        # Floor at one second so a degenerate "already expired" call
        # doesn't store a negative TTL (which we'd then immediately
        # treat as not-revoked on the next ``is_revoked`` check).
        ttl_seconds = max(1, int(ttl_seconds))
        async with self._lock:
            self._entries[jti] = time.time() + ttl_seconds

    async def is_revoked(self, jti: str) -> bool:
        async with self._lock:
            expiry = self._entries.get(jti)
            if expiry is None:
                return False
            if time.time() >= expiry:
                # Lazy GC — pop expired entries on read so the dict
                # doesn't grow without bound.
                self._entries.pop(jti, None)
                return False
            return True

    async def reset(self) -> None:
        """Test-only — wipe state. Not part of the Protocol."""
        async with self._lock:
            self._entries.clear()


# ---------------------------------------------------------------------------
# Redis backend
# ---------------------------------------------------------------------------
class _RedisDenylist:
    """Redis-backed deny-list. Lazy-imports ``redis.asyncio`` so the
    package is only required when actually used."""

    def __init__(self, url: str) -> None:
        # Import locally so missing the optional ``redis`` package only
        # bites when REDIS_URL is configured.
        from redis.asyncio import from_url

        self._client = from_url(url, encoding="utf-8", decode_responses=True)

    async def revoke(self, jti: str, ttl_seconds: int) -> None:
        ttl_seconds = max(1, int(ttl_seconds))
        try:
            await self._client.set(_REDIS_KEY_PREFIX + jti, "1", ex=ttl_seconds)
        except Exception:
            # Don't kill the request because the deny-list write failed
            # — log and continue. The token will simply not be revoked
            # on this replica's view of Redis.
            _log.exception("denylist_revoke_failed", jti=jti)

    async def is_revoked(self, jti: str) -> bool:
        try:
            value = await self._client.get(_REDIS_KEY_PREFIX + jti)
        except Exception:
            # Fail-open: if Redis is unreachable, allow the request to
            # proceed rather than locking everyone out. This is a
            # deliberate availability-over-paranoia tradeoff; pair with
            # alerting on the structlog event.
            _log.exception("denylist_check_failed_failing_open", jti=jti)
            return False
        return value is not None


# ---------------------------------------------------------------------------
# Singleton wiring
# ---------------------------------------------------------------------------
_denylist: TokenDenylist | None = None


def get_denylist() -> TokenDenylist:
    """Return (and lazily build) the process-wide deny-list.

    Picks the backend based on ``settings.redis_url`` at first call.
    Tests can override by calling :func:`set_denylist`.
    """
    global _denylist
    if _denylist is None:
        settings = get_settings()
        if settings.redis_url:
            _denylist = _RedisDenylist(settings.redis_url)
        else:
            _denylist = _InMemoryDenylist()
    return _denylist


def set_denylist(backend: TokenDenylist | None) -> None:
    """Override (or reset) the deny-list singleton. Test helper.

    Pass ``None`` to clear the singleton so the next ``get_denylist``
    call rebuilds from settings.
    """
    global _denylist
    _denylist = backend
