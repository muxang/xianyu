"""Tests for app.main HTTP endpoints and lifespan."""

from __future__ import annotations

import asyncio
from typing import Any, Never

import pytest
from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok() -> None:
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_db_returns_ok() -> None:
    """Real DB reachability check; relies on dev Postgres being up."""
    with TestClient(app) as client:
        resp = client.get("/health/db")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": "connected"}


def test_health_db_returns_500_when_db_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure response is sanitized; raw DB error text never leaks to client."""
    sentinel = "leaked_secret_DO_NOT_EXPOSE_xyz"

    def explosive_maker() -> Never:
        raise RuntimeError(f"connection refused: password={sentinel}")

    monkeypatch.setattr("app.main.get_session_maker", explosive_maker)

    with TestClient(app) as client:
        resp = client.get("/health/db")

    assert resp.status_code == 500
    assert resp.json() == {"status": "error", "detail": "database unavailable"}
    # Sanity: detail string is exactly the sanitized message; no leak via body.
    assert sentinel not in resp.text


def test_health_db_returns_503_when_db_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow SELECT is cancelled by asyncio.wait_for and served as 503."""

    class _SlowSession:
        async def execute(self, *_args: Any, **_kwargs: Any) -> None:
            # Sleep longer than the test-shrunk timeout so wait_for fires.
            await asyncio.sleep(1.0)

        async def __aenter__(self) -> _SlowSession:
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

    # Shrink the real timeout so the test runs in ~10 ms instead of 3 s.
    monkeypatch.setattr("app.main._DB_HEALTHCHECK_TIMEOUT", 0.01)
    # Patched maker returns the SlowSession class directly (it's usable as an
    # async context manager whether invoked as `maker()` or used as `maker`).
    monkeypatch.setattr("app.main.get_session_maker", lambda: _SlowSession)

    with TestClient(app) as client:
        resp = client.get("/health/db")

    assert resp.status_code == 503
    assert resp.json() == {"status": "error", "detail": "database unavailable"}


def test_app_lifespan_starts_and_shuts_down() -> None:
    """TestClient context manager invokes startup + shutdown without raising."""
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
