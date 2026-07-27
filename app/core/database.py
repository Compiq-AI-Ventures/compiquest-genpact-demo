"""SQLAlchemy 2.x async engine, session factory, and declarative Base.

This module is the single place that knows how to construct database
primitives. Other modules should import:

    * ``Base``                — to declare ORM models.
    * ``AsyncSessionLocal``  — when a session is needed outside a request
                                (scripts, background jobs, tests).

For FastAPI routes, use ``app.dependencies.db_dependency.get_db`` instead of
touching ``AsyncSessionLocal`` directly.
"""

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
# ``create_async_engine`` returns an ``AsyncEngine``. ``pool_pre_ping`` issues a
# lightweight ping before handing out a connection so stale ones (e.g. dropped
# by Postgres after an idle timeout) are recycled transparently.
engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
)

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------
# ``expire_on_commit=False`` keeps attributes accessible after commit, which is
# the friendlier default for FastAPI request handlers that often serialize ORM
# objects to JSON after the transaction has closed.
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


# ---------------------------------------------------------------------------
# Declarative Base (SQLAlchemy 2.x style)
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    """Base class for all ORM models.

    Subclass this and use ``Mapped[...]`` / ``mapped_column(...)`` to declare
    columns, e.g.::

        class User(Base):
            __tablename__ = "users"
            id: Mapped[int] = mapped_column(primary_key=True)
            email: Mapped[str] = mapped_column(unique=True)
    """
