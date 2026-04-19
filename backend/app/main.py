"""FastAPI entrypoint."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.db.session import dispose_engine, get_engine, get_session_maker

log = structlog.get_logger(__name__)

# Exposed as a module-level constant so tests can monkeypatch it to a
# tiny value and drive the real asyncio.wait_for timeout path without
# waiting seconds per run.
_DB_HEALTHCHECK_TIMEOUT = 3.0


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    log.info("app_starting", app_env=settings.APP_ENV)
    # Pre-construct the engine before serving requests so two concurrent
    # first-callers cannot race to build two engines (TOCTOU on the
    # module-level singleton). FastAPI runs lifespan startup before the
    # server accepts connections, so this call is guaranteed serial.
    get_engine()
    log.info("engine_initialized")
    yield
    await dispose_engine()
    log.info("app_stopped")


app = FastAPI(title="Xianyu AI Service", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/db", response_model=None)
async def health_db() -> JSONResponse | dict[str, str]:
    """Probe DB reachability via SELECT 1.

    Errors are logged with full detail server-side but the response
    body never carries DB-level error text (which can leak passwords
    or internal paths).
    """
    try:
        maker = get_session_maker()
        async with maker() as session:
            result = await asyncio.wait_for(
                session.execute(text("SELECT 1")),
                timeout=_DB_HEALTHCHECK_TIMEOUT,
            )
            assert result.scalar() == 1
        return {"status": "ok", "db": "connected"}
    except TimeoutError:
        log.warning("healthcheck_db_timeout", timeout_s=_DB_HEALTHCHECK_TIMEOUT)
        return JSONResponse(
            status_code=503,
            content={"status": "error", "detail": "database unavailable"},
        )
    except Exception as e:
        log.error("healthcheck_db_failed", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": "database unavailable"},
        )
