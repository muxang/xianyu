"""Tests for app.db.session.

Per Step B guidance: unit tests drive ``get_session_maker()``
directly. Integration of ``get_session()`` (the FastAPI dependency)
goes through an HTTP endpoint in Step C; do not iterate the async
generator manually here, since exceptions raised inside ``async for``
on a generator can be swallowed.

Behavioral commit / rollback is verified by writing through the
``_health_check`` table that the dev init script created
(infra/docker/init-scripts/01-init.sql).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text

from app.db.session import dispose_engine, get_engine, get_session_maker


@pytest.fixture(autouse=True)
async def _cleanup_engine() -> AsyncGenerator[None, None]:
    """Dispose engine + maker before AND after every test.

    Pre-dispose guards against leakage from test modules that ran
    earlier and forgot to clean up; post-dispose keeps the next test
    from inheriting our state.
    """
    await dispose_engine()
    yield
    await dispose_engine()


async def test_get_engine_is_singleton() -> None:
    a = get_engine()
    b = get_engine()
    assert a is b


async def test_get_session_maker_is_singleton() -> None:
    a = get_session_maker()
    b = get_session_maker()
    assert a is b


async def test_session_maker_has_expire_on_commit_false() -> None:
    """Config check: full behavioral test (DetachedInstanceError) waits for ORM models."""
    maker = get_session_maker()
    async with maker() as session:
        assert session.sync_session.expire_on_commit is False


async def test_get_session_executes_query() -> None:
    """Smoke test: connect through the maker and run SELECT 1."""
    maker = get_session_maker()
    async with maker() as session:
        result = await session.execute(text("SELECT 1"))
        assert result.scalar() == 1


async def test_get_session_commits_on_success() -> None:
    """Committed write in one session is visible in a fresh session."""
    maker = get_session_maker()

    async with maker() as setup:
        await setup.execute(text("DELETE FROM _health_check"))
        await setup.commit()

    async with maker() as session:
        await session.execute(text("INSERT INTO _health_check DEFAULT VALUES"))
        await session.commit()

    async with maker() as verify:
        result = await verify.execute(text("SELECT count(*) FROM _health_check"))
        assert result.scalar() == 1
        await verify.execute(text("DELETE FROM _health_check"))
        await verify.commit()


async def test_get_session_rollbacks_on_exception() -> None:
    """Uncommitted write is dropped when the session exits on an exception."""
    maker = get_session_maker()

    async with maker() as setup:
        await setup.execute(text("DELETE FROM _health_check"))
        await setup.commit()

    with pytest.raises(RuntimeError, match="boom"):
        async with maker() as session:
            await session.execute(text("INSERT INTO _health_check DEFAULT VALUES"))
            raise RuntimeError("boom")

    async with maker() as verify:
        result = await verify.execute(text("SELECT count(*) FROM _health_check"))
        assert result.scalar() == 0


async def test_dispose_engine_resets_singleton() -> None:
    """After dispose, get_engine returns a fresh instance and the maker still works."""
    first = get_engine()
    await dispose_engine()

    second = get_engine()
    assert first is not second

    new_maker = get_session_maker()
    async with new_maker() as session:
        result = await session.execute(text("SELECT 1"))
        assert result.scalar() == 1
