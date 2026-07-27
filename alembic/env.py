"""Alembic environment for the CompIQCoreBe project (JVRE backend).

This script is invoked by ``alembic`` for every command (``revision``,
``upgrade``, ``downgrade``, ``current``, ``history``, ...). It is responsible
for:

1. Loading the database URL from the application's settings (NOT from
   alembic.ini), so credentials stay out of source control and match the
   running app.
2. Telling Alembic which ``MetaData`` to compare against when autogenerating
   migrations (``Base.metadata`` from ``app.core.database``).
3. Bridging Alembic's synchronous migration runner to our async SQLAlchemy
   engine via ``connection.run_sync(...)``.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context

# ---------------------------------------------------------------------------
# Model discovery
# ---------------------------------------------------------------------------
# Alembic's autogenerate compares ``Base.metadata`` to the live database. A
# model only appears in ``Base.metadata`` once its module has been imported,
# so importing the ``app.models`` package (which re-exports every model) is
# all that's needed — add new model modules to ``app/models/__init__.py``
# and they'll be picked up automatically.
from app import models  # noqa: F401
from app.core.config import get_settings
from app.core.database import Base
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# ---------------------------------------------------------------------------
# Alembic config
# ---------------------------------------------------------------------------
config = context.config
settings = get_settings()

# NOTE: we deliberately do NOT call ``config.set_main_option(
# "sqlalchemy.url", settings.database_url)`` here — alembic stores
# config in a configparser, which treats ``%`` as an interpolation
# character. A perfectly valid percent-encoded URL like
# ``...:Alpha%26252@...`` (the URL-encoding of ``Alpha&252``) would
# raise ``ValueError: invalid interpolation syntax``. The engine is
# built directly from ``settings.database_url`` below so the URL
# never round-trips through configparser.

# Configure Python logging from the [loggers] / [handlers] / [formatters]
# sections in alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The MetaData object Alembic compares against the live schema.
target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Offline migrations: emit raw SQL, no DB connection required.
# Useful for handing a SQL script to a DBA, or for review.
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    """Run migrations without connecting to a database."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online migrations: connect to the database and apply changes.
# Bridged from sync (Alembic) to async (our engine) via run_sync().
# ---------------------------------------------------------------------------
def do_run_migrations(connection: Connection) -> None:
    """Run migrations against an active sync Connection (provided by run_sync).

    Transaction management
    ----------------------
    Every statement we run here MUST happen inside the
    ``context.begin_transaction()`` block. If ANY statement runs first,
    SQLAlchemy 2.0's "begin once" mode auto-begins an outer
    transaction; ``context.begin_transaction()`` then opens a SAVEPOINT
    inside it; the savepoint commits but the outer transaction never
    does, and the entire migration silently rolls back when the
    connection closes. (We learned this the hard way.)
    """
    from sqlalchemy import text as _sa_text

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Detect column TYPE changes (e.g. VARCHAR(50) -> VARCHAR(100)).
        compare_type=True,
        # Detect server-side default changes.
        compare_server_default=True,
    )
    with context.begin_transaction():
        # Bypass tenant-scoped Row-Level Security for the duration of
        # the migration. Without this, migrations that touch
        # RLS-enabled tables (departments, etc.) fail because the
        # migration runner is not "any tenant" — the predicate is
        # false for every row. Must run INSIDE begin_transaction (see
        # docstring above).
        connection.execute(_sa_text("SET app.platform_override = 'true'"))
        context.run_migrations()


async def run_async_migrations() -> None:
    """Build an async engine from settings and run migrations."""
    # Build the engine directly from settings rather than threading
    # the URL through alembic's configparser-backed config — see the
    # NOTE near the top of this file for why.
    connectable = create_async_engine(
        settings.database_url,
        # NullPool: don't keep connections around after the migration ends.
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Entrypoint for online mode — runs the async migration coroutine."""
    asyncio.run(run_async_migrations())


# ---------------------------------------------------------------------------
# Dispatch based on the mode Alembic was invoked in.
# ---------------------------------------------------------------------------
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
