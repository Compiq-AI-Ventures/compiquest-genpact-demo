"""Domain exceptions and global exception handlers.

Two responsibilities:

* :class:`DomainError` — the base class for application-defined exceptions
  raised by services. Subclasses set ``status_code`` and ``error_code``
  so the global handler can render them uniformly.

* :func:`register_exception_handlers` — attaches handlers to the FastAPI
  app that wrap **every** error response in the standard envelope from
  :func:`app.utils.response_builder.error_response`. Validation errors,
  HTTPExceptions, DomainErrors, and uncaught exceptions all come out
  the same shape so clients have one branch to handle.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.utils.response_builder import error_response

_log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------
class DomainError(Exception):
    """Base for application-defined exceptions raised by services.

    Subclass it and set ``status_code`` / ``error_code`` to control how
    the global handler renders the response. ``message`` is the
    human-readable text returned to the client; ``details`` is an optional
    structured payload (e.g. field errors). Sensitive context (real email
    addresses, internal identifiers) belongs in instance attributes
    consumed by the logging layer — NOT in ``message`` or ``details``,
    which are returned over the wire.
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code: str = "DOMAIN_ERROR"

    def __init__(
        self,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or self.error_code)
        self.message = message or self.error_code
        self.details = details or {}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
async def _handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
    """Render any :class:`DomainError` in the standard envelope.

    Adds ``WWW-Authenticate: Bearer`` on 401s so OAuth2 / Bearer clients
    learn which auth scheme to retry with (RFC 6750 §3 / RFC 7235 §3.1).
    The ``_handle_http_exception`` branch already preserves this header
    via ``exc.headers``; this branch sets it unconditionally for the
    401 status to keep DomainError-driven 401s spec-compliant too.
    """
    _log.warning(
        "domain_error",
        error_code=exc.error_code,
        status_code=exc.status_code,
        path=request.url.path,
        method=request.method,
    )
    headers: dict[str, str] | None = None
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        headers = {"WWW-Authenticate": "Bearer"}
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(
            error_code=exc.error_code,
            message=exc.message,
            details=exc.details,
        ),
        headers=headers,
    )


async def _handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    """Re-emit FastAPI ``HTTPException``s in the envelope shape.

    Preserves any custom headers (e.g. ``WWW-Authenticate: Bearer`` on
    401s) so OAuth2 clients still work.
    """
    error_code = _http_status_to_error_code(exc.status_code)
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    _log.info(
        "http_exception",
        error_code=error_code,
        status_code=exc.status_code,
        path=request.url.path,
        method=request.method,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(error_code=error_code, message=detail),
        headers=getattr(exc, "headers", None),
    )


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Render Pydantic 422s in the envelope, with field errors in ``details``."""
    # Pydantic ``ValidationError`` items contain non-JSON-safe types
    # (e.g. ``ctx`` may include exception instances). Use exc.errors() and
    # filter to JSON-safe fields.
    errors = [
        {
            "loc": list(err.get("loc", [])),
            "msg": err.get("msg", ""),
            "type": err.get("type", ""),
        }
        for err in exc.errors()
    ]
    _log.info(
        "validation_error",
        path=request.url.path,
        method=request.method,
        error_count=len(errors),
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=error_response(
            error_code="VALIDATION_ERROR",
            message="Request validation failed.",
            details={"errors": errors},
        ),
    )


async def _handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unanticipated errors. Logs the stack trace; returns 500.

    The client gets a generic message (no stack trace, no internal
    identifiers) — operators inspect logs by ``request_id``.
    """
    _log.exception(
        "unexpected_error",
        path=request.url.path,
        method=request.method,
        exc_type=exc.__class__.__name__,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_response(
            error_code="INTERNAL_ERROR",
            message="An unexpected error occurred. Please try again or contact support.",
        ),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all global exception handlers to ``app``."""
    app.add_exception_handler(DomainError, _handle_domain_error)
    app.add_exception_handler(HTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected_exception)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_HTTP_STATUS_ERROR_CODES: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    410: "GONE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    502: "BAD_GATEWAY",
    503: "SERVICE_UNAVAILABLE",
    504: "GATEWAY_TIMEOUT",
}


def _http_status_to_error_code(status_code: int) -> str:
    """Map an HTTP status code to a stable, machine-readable error code."""
    return _HTTP_STATUS_ERROR_CODES.get(status_code, f"HTTP_{status_code}")
