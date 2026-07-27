"""Standard envelope shape for API responses.

Every JSON response from a route handler should pass through one of
these two functions so the wire format is consistent across the API:

Success::

    {
        "status": "success",
        "message": "<human readable>",
        "data": <payload>
    }

Error::

    {
        "status": "fail",
        "error_code": "<machine readable code>",
        "message": "<human readable>",
        "details": { ... }
    }

Note: ``error_response`` is provided for future custom-exception
handlers. Today, errors raised via ``HTTPException`` still produce
FastAPI's default ``{"detail": "..."}`` shape — we'll migrate those
deliberately when we land a global exception handler.
"""

from __future__ import annotations

from typing import Any


def success_response(message: str, data: Any) -> dict[str, Any]:
    """Wrap a successful payload in the standard envelope.

    Args:
        message: Human-readable message describing the outcome.
        data: The payload. Accepts a dict, a Pydantic model, a list, or
            any other JSON-serializable value — FastAPI's response
            encoder converts the result for the wire.

    Returns:
        A dict ready to return from a FastAPI route handler.
    """
    return {
        "status": "success",
        "message": message,
        "data": data,
    }


def error_response(
    error_code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap an error payload in the standard envelope.

    Args:
        error_code: Machine-readable identifier for the error
            (e.g. ``"EMAIL_ALREADY_EXISTS"``). Clients should branch
            on this, not on the human message.
        message: Human-readable description of what went wrong.
        details: Optional structured information (field-level errors,
            offending values, etc.). Defaults to an empty dict so
            clients can always read ``response.details["..."]``.
    """
    return {
        "status": "fail",
        "error_code": error_code,
        "message": message,
        "details": details or {},
    }
