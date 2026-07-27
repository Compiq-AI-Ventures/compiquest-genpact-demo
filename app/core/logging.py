"""Structured (JSON) logging configuration.

Wraps stdlib ``logging`` with structlog so:

* Every log line is JSON: easy to ship to ELK, Loki, Datadog, etc.
* Context bound via ``structlog.contextvars.bind_contextvars`` (e.g. the
  request ID) automatically appears in every log line emitted within
  that context — including logs from libraries that use stdlib
  ``logging`` (uvicorn, sqlalchemy) once they're routed through
  structlog's ``ProcessorFormatter``.

Call :func:`configure_logging` exactly once at application start.
"""

from __future__ import annotations

import logging
import sys

import structlog

from app.core.config import get_settings


def configure_logging() -> None:
    """Initialise stdlib + structlog. Idempotent — safe to call repeatedly."""
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Shared processors used for both structlog calls and stdlib log
    # records that are routed through structlog's ProcessorFormatter.
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
    ]

    # ----- structlog ---------------------------------------------------
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # ----- stdlib ------------------------------------------------------
    # Route stdlib logs (uvicorn, sqlalchemy, asyncpg, etc.) through
    # structlog's ProcessorFormatter so they share the same JSON shape
    # and pick up bound context vars.
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=shared_processors,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Replace any existing handlers so we don't double-log.
    root.handlers = [handler]
    root.setLevel(level)

    # Silence noisy library loggers in production-ish environments.
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).setLevel(logging.WARNING if level <= logging.INFO else level)
