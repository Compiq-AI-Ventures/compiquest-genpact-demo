"""HTTP middleware: request IDs, structured access logs, security headers.

Order of registration matters in Starlette — middleware added LAST runs
FIRST on the way in. :func:`register_middleware` adds them so the final
request flow is::

    CORS  →  RequestID  →  AccessLog  →  SecurityHeaders  →  router
"""

from __future__ import annotations

import time
import uuid

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings

_log = structlog.get_logger(__name__)

# Header used to propagate / surface the request ID. ``X-Request-ID`` is
# the de-facto convention; we accept it on the way in and always set it
# on the way out so clients can quote it in support tickets.
REQUEST_ID_HEADER = "X-Request-ID"


# ---------------------------------------------------------------------------
# Request ID
# ---------------------------------------------------------------------------
class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach an X-Request-ID to every request and bind it to log context.

    Honors an inbound ``X-Request-ID`` (so an upstream LB / gateway can
    seed it for end-to-end correlation); generates a UUID4 hex if absent.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        # Also expose it on request.state for handlers that want it.
        request.state.request_id = request_id

        # Bind to structlog's context vars — every log line emitted in
        # this request's task tree picks it up automatically.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        try:
            response = await call_next(request)
        finally:
            # Don't leak context to the next request handled by this worker.
            structlog.contextvars.clear_contextvars()

        response.headers[REQUEST_ID_HEADER] = request_id
        return response


# ---------------------------------------------------------------------------
# Access log
# ---------------------------------------------------------------------------
class AccessLogMiddleware(BaseHTTPMiddleware):
    """Emit one structured log line per request with method/path/status/latency."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Log the failure with timing, then re-raise so the global
            # exception handler can shape the response.
            latency_ms = (time.perf_counter() - start) * 1000
            _log.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                client=_client_host(request),
                latency_ms=round(latency_ms, 2),
            )
            raise

        latency_ms = (time.perf_counter() - start) * 1000
        _log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            client=_client_host(request),
            latency_ms=round(latency_ms, 2),
        )
        return response


def _client_host(request: Request) -> str | None:
    """Return the client host for logging, or ``None`` if unknown."""
    return request.client.host if request.client else None


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add a sensible default set of security headers to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        # Block MIME-type sniffing.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # Disallow framing (clickjacking defense).
        response.headers.setdefault("X-Frame-Options", "DENY")
        # Limit referrer info exposed to third parties.
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # Disable powerful APIs we don't use.
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        # HSTS only in production — sending it in dev (over plain HTTP)
        # would pin localhost to HTTPS forever in browsers that visit it.
        if get_settings().is_production:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def register_middleware(app: FastAPI) -> None:
    """Wire up CORS + custom middleware on ``app``.

    Reminder: Starlette runs middleware in REVERSE order of registration.
    The final on-the-wire ordering is the docstring at the top of this
    module.
    """
    settings = get_settings()

    # Innermost — security headers wrap the response on its way out.
    app.add_middleware(SecurityHeadersMiddleware)

    # Logs the request after it returns from the router.
    app.add_middleware(AccessLogMiddleware)

    # Generates / propagates the request ID; binds it to structlog context.
    app.add_middleware(RequestIDMiddleware)

    # Outermost — handles CORS preflight before any of the above touches it.
    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=settings.cors_allow_credentials,
            allow_methods=settings.cors_allow_methods,
            allow_headers=settings.cors_allow_headers,
            # Content-Disposition lets the SPA read the attachment filename
            # when it fetches a download (e.g. the compensation report PDF)
            # with the bearer token and saves the blob.
            expose_headers=[REQUEST_ID_HEADER, "Content-Disposition", "X-CompChat-Trace-Id"],
        )
