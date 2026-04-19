"""Alembic migration environment (async).

Uses ``sqlalchemy.ext.asyncio.create_async_engine`` against the URL in
``app.config.settings.DATABASE_URL`` (which must use the +asyncpg
driver). Online migrations run inside ``connection.run_sync`` so the
sync Alembic API works against an async connection.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.db.base import Base

# Alembic Config object, providing access to alembic.ini values.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Source of truth for autogenerate diffs.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Render migration SQL to stdout without connecting to a DB."""
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Sync callable invoked inside ``connection.run_sync``."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Open an async engine, run migrations through a sync bridge."""
    connectable = create_async_engine(settings.DATABASE_URL)
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
