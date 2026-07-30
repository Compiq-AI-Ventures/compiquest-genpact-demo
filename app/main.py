"""FastAPI application entry point.

Order of bootstrap matters:

1. ``configure_logging()`` first so any errors during the rest of
   bootstrap are emitted as JSON.
2. Construct the FastAPI app.
3. Register middleware (security headers, request ID, access log, CORS).
4. Register the rate limiter.
5. Register the global exception handlers.
6. Mount routers.
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.core.config import get_settings
from app.core.database import engine
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import register_middleware
from app.core.rate_limit import register_rate_limiter
from app.dependencies.db_dependency import get_db
from app.routers import (
    admin_router,
    auth_router,
    department_router,
    iquest_ai_router,
    jvre_workspace_router,
    pnl_dashboard_router,
    tenant_router,
)

# 1. Logging — must happen before anything else so config / startup
#    errors come out as JSON in the same shape as runtime logs.
configure_logging()

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup and shutdown events."""
    # Startup
    yield
    # Shutdown — dispose of the connection pool cleanly.
    await engine.dispose()


# 2. App
app = FastAPI(
    title="CompIQCoreBe",
    description="Backend for JVRE (Job Value Recommendation Engine)",
    version=__version__,
    lifespan=lifespan,
    # Disable interactive docs and the OpenAPI schema in production.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

# 3. Middleware (security headers, request ID, access log, CORS).
register_middleware(app)

# 4. Rate limiter (slowapi) — attaches limiter state, middleware, 429 handler.
register_rate_limiter(app)

# 5. Global exception handlers — wrap every error response in the
#    standard envelope. Must be registered AFTER the rate limiter so
#    its specific RateLimitExceeded handler wins for 429s.
register_exception_handlers(app)

# 6. Routers
app.include_router(auth_router.router)
app.include_router(admin_router.router)
app.include_router(tenant_router.router)
app.include_router(department_router.router)

# JVRE workspace — five sub-routers under the same tag, mounted as one
# logical surface in /docs.
app.include_router(jvre_workspace_router.cycle_router)
app.include_router(jvre_workspace_router.allocation_router)
app.include_router(jvre_workspace_router.recommendation_router)
app.include_router(jvre_workspace_router.jvre_router)
app.include_router(jvre_workspace_router.user_reference_router)

# iQuest AI — suggested questions + Q&A streaming
app.include_router(iquest_ai_router.router)

# P&L Head — org-wide Executive Summary dashboard
app.include_router(pnl_dashboard_router.router)


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint."""
    return {"message": "Welcome to CompIQCoreBe", "version": __version__}


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — returns OK if the process is up."""
    return {"status": "healthy"}


@app.get("/health/db")
async def health_db(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    """Readiness probe — verifies the database is reachable by running SELECT 1."""
    result = await db.execute(text("SELECT 1"))
    value = result.scalar_one()
    return {"status": "healthy", "database": "connected", "result": str(value)}
