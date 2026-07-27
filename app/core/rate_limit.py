"""Rate-limiting primitives backed by slowapi.

Single :data:`limiter` instance shared across the app. Routes opt in
with the ``@limiter.limit("...")`` decorator (see ``auth_router``).

Storage backend is selected by ``RATE_LIMIT_STORAGE_URI``:

* ``memory://``         — per-process counters (good for single-pod dev).
* ``redis://host:port/0`` — shared counters across instances. Required
  for multi-pod deployments; otherwise an attacker just retries until
  they hit a fresh worker.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler  # noqa: F401  (re-exported below)
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.core.config import get_settings
from app.utils.response_builder import error_response

_log = structlog.get_logger(__name__)

# The Limiter is imported by routes (for ``@limiter.limit(...)``) and by
# ``register_rate_limiter`` below to attach to the app. Keep it module-level.
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=get_settings().rate_limit_storage_uri,
    # Don't tag every route as "limited by default" — we opt in.
    default_limits=[],
)


async def _rate_limited_response(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Render 429s in the standard error envelope."""
    _log.warning(
        "rate_limited",
        path=request.url.path,
        method=request.method,
        client=request.client.host if request.client else None,
        limit=str(exc.detail),
    )
    return JSONResponse(
        status_code=429,
        content=error_response(
            error_code="RATE_LIMITED",
            message="Too many requests. Please slow down and try again shortly.",
            details={"limit": str(exc.detail)},
        ),
    )


def register_rate_limiter(app: FastAPI) -> None:
    """Attach the limiter, its middleware, and the 429 handler to ``app``."""
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)
    app.add_exception_handler(RateLimitExceeded, _rate_limited_response)
