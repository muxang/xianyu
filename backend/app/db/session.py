"""Async SQLAlchemy engine and session factories.

Module-level singletons for the engine and session maker. Use
``get_session()`` as a FastAPI dependency in route handlers; for
unit/integration tests, drive ``get_session_maker()`` directly to
avoid running the dependency's async-generator commit/rollback
wrapper outside of FastAPI's lifecycle (which would swallow
exceptions).
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

_engine: AsyncEngine | None = None
_session_maker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first call."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.DATABASE_URL,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,  # detect Postgres-side idle disconnects
            echo=(
                settings.APP_ENV == "development"
                and settings.APP_LOG_LEVEL == "DEBUG"
            ),
        )
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async sessionmaker, creating it on first call."""
    global _session_maker
    if _session_maker is None:
        _session_maker = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,  # required so async callers can read attrs after commit
        )
    return _session_maker


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: commits on success, rolls back on exception."""
    async with get_session_maker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Close the engine and reset both singletons.

    The session maker holds a reference to the engine, so resetting it
    keeps callers from using a maker bound to a disposed engine. Safe
    to call multiple times.
    """
    global _engine, _session_maker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
    _session_maker = None
