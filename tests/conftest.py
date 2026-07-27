"""Shared pytest fixtures for the test suite.

Test isolation strategy
-----------------------
We point a separate engine at a dedicated test database (configurable via
``TEST_DATABASE_URL``). At session start we drop and recreate every table
defined on ``Base.metadata``. Before each test we ``TRUNCATE`` the user
table to guarantee a clean slate, then yield a single ``AsyncSession``
that is shared between the test code and the FastAPI handler (via a
``get_db`` dependency override).

This is simpler than a savepoint-rollback strategy and fast enough for a
suite of this size; if the suite grows large we can switch to nested
transactions per test.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from app.core.database import Base
from app.core.rate_limit import limiter
from app.core.roles import DEFAULT_ROLES
from app.core.token_denylist import set_denylist
from app.dependencies.db_dependency import get_db
from app.main import app
from app.models.role import Role
from app.services.audit_log_service import set_independent_session_factory
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

# Disable IP-based rate limiting for the entire test suite. Every test
# hits localhost from the same client, so the production limits would
# starve any test that calls a limited endpoint more than a handful of
# times. Tests that specifically exercise the limiter should re-enable
# it locally with ``limiter.enabled = True`` inside the test (and
# ``limiter.reset()`` to clear counters).
limiter.enabled = False

# The test database URL must be supplied via the environment. There is
# DELIBERATELY no fallback — credentials of any kind (even dummy ones)
# in committed code are an anti-pattern, and a hardcoded fallback risks
# tests silently running against the wrong database when the env var is
# missing.
#
# Set it before running pytest, e.g.:
#
#     PowerShell:
#       $env:TEST_DATABASE_URL = "postgresql+asyncpg://USER:PASSWORD@host:5432/dbname"
#     bash:
#       export TEST_DATABASE_URL="postgresql+asyncpg://USER:PASSWORD@host:5432/dbname"
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    raise RuntimeError(
        "TEST_DATABASE_URL environment variable is required for tests. "
        "Set it to a connection string for a dedicated test database, e.g. "
        "postgresql+asyncpg://USER:PASSWORD@localhost:5432/compiqcorebe_test"
    )

# Tables that carry ``tenant_id`` directly and have RLS enabled in
# production. Mirrors the production migration list so the test schema
# matches. Add new RLS-protected tables here AND in the corresponding
# migration. FK-isolated children (budget_allocation_lines,
# pay_recommendation_components, etc.) inherit isolation through their
# parent and are deliberately excluded.
_RLS_TABLES: tuple[str, ...] = (
    "departments",
    "compensation_cycles",
    "reporting_relationships",
    "budget_allocations",
    "pay_recommendations",
    "jvre_snapshots",
    "market_benchmarks",
    "compensation_history",
)


@pytest_asyncio.fixture(scope="session")
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Build an engine for the test DB and (re)create the schema once per session."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    # Importing ``app.models`` ensures every model module is imported, so
    # all tables are present on Base.metadata before create_all().
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        # ``create_all`` doesn't apply RLS — those policies live in
        # the migration. Recreate the policy here so the test schema
        # mirrors prod. Add new RLS-protected tables to ``_RLS_TABLES``
        # below as they land.
        for table_name in _RLS_TABLES:
            await conn.execute(
                text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
            )
            await conn.execute(
                text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
            )
            await conn.execute(
                text(
                    f"""
                    CREATE POLICY tenant_isolation ON {table_name}
                        FOR ALL
                        USING (
                            tenant_id::text = current_setting('app.current_tenant', true)
                            OR current_setting('app.platform_override', true) = 'true'
                        )
                        WITH CHECK (
                            tenant_id::text = current_setting('app.current_tenant', true)
                            OR current_setting('app.platform_override', true) = 'true'
                        )
                    """
                )
            )

        # ``test_rls_blocks_cross_tenant_select`` needs to actually
        # exercise the policy — but Postgres superusers and roles with
        # the BYPASSRLS attribute bypass RLS unconditionally (FORCE
        # ROW LEVEL SECURITY only constrains the table owner, not
        # superusers). Tests typically connect as ``postgres``, so we
        # provision a stripped-down role the RLS test can ``SET ROLE``
        # to. The role only needs to exist in the test cluster; the
        # test then becomes a real defense-in-depth proof.
        role_exists = await conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = 'rls_tester'")
        )
        if role_exists.scalar_one_or_none() is None:
            await conn.execute(
                text(
                    "CREATE ROLE rls_tester NOLOGIN NOSUPERUSER NOBYPASSRLS"
                )
            )
        await conn.execute(text("GRANT USAGE ON SCHEMA public TO rls_tester"))
        # Mirror the per-table SELECT grants from the migration.
        await conn.execute(
            text(
                "GRANT SELECT ON " + ", ".join(_RLS_TABLES) + " TO rls_tester"
            )
        )

    # Seed the well-known role rows once per session. Tests that need
    # additional ad-hoc roles can insert them inside the test using
    # ``db_session``; truncating ``users`` between tests cascades to
    # ``user_roles`` but leaves the seed roles intact.
    seed_session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with seed_session_factory() as seed_session:
        for code, name, description, scope in DEFAULT_ROLES:
            seed_session.add(
                Role(
                    code=code,
                    name=name,
                    description=description,
                    scope=scope,
                    is_system_role=True,
                )
            )
        await seed_session.commit()

    # Route ``audit_log_service.log_action_independent`` at the test
    # engine. Without this it would open sessions against the
    # production ``DATABASE_URL`` — failed-login / access-denied audit
    # rows would land in the wrong database and tests asserting on
    # them would fail mysteriously.
    set_independent_session_factory(
        async_sessionmaker(bind=engine, expire_on_commit=False)
    )

    yield engine

    set_independent_session_factory(None)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_token_denylist() -> None:
    """Drop the JWT deny-list singleton between tests.

    Without this, a revoked token (or even just an unrelated jti
    accumulated across tests) leaks between tests and can flake
    auth-dependent assertions. Setting it back to ``None`` forces
    ``get_denylist`` to rebuild a fresh in-memory backend on next use.
    """
    set_denylist(None)


@pytest_asyncio.fixture
async def db_session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Yield a clean ``AsyncSession`` with mutable tables truncated.

    Roles (the seed data) are intentionally NOT truncated — they're
    populated once at session start and reused across every test.
    Tenants ARE truncated so a test that creates them doesn't bleed
    into the next test.
    """
    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    async with session_factory() as session:
        # Grant this session the RLS bypass so test setup can write to
        # tenant-scoped tables (e.g. departments) without first
        # resolving a tenant context. The test client's request flow
        # still goes through ``get_tenant_scoped_db``; this only
        # affects direct ``db_session`` usage in test code.
        await session.execute(text("SET app.platform_override = 'true'"))

        # CASCADE clears child rows: deleting users wipes user_roles;
        # deleting tenants wipes user_roles + departments + the tenant
        # user rows themselves (via users.tenant_id FK CASCADE).
        # ``audit_logs`` has SET NULL FKs so it isn't pulled in by the
        # cascade — truncate it explicitly. RESTART IDENTITY is
        # harmless for UUID PKs.
        await session.execute(
            text(
                "TRUNCATE TABLE audit_logs, departments, users, tenants "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
        yield session


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client bound to the FastAPI app, sharing the test ``db_session``.

    Mirrors the production Unit-of-Work contract from
    :func:`app.dependencies.db_dependency.get_db`: the service layer
    flushes only, and this override commits on a clean return / rolls
    back on a raise. Without that mirroring, a test would see
    flushed-but-uncommitted data within the request session but the
    production code would behave differently — exactly the kind of
    silent drift that masks real bugs.

    The session itself is owned by the outer ``db_session`` fixture so
    we don't close it here.
    """

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        try:
            yield db_session
        except Exception:
            await db_session.rollback()
            raise
        else:
            await db_session.commit()

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    app.dependency_overrides.clear()
