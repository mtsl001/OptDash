"""Entry point -- starts FastAPI + APScheduler.

Usage:  python run_api.py

Lifespan here owns both DB connections (via deps.startup/shutdown)
and the scheduler.  The create_app() factory receives this lifespan
so there is no double-initialisation.

DuckDB note:
  deps.startup() calls duckdb_gateway.startup() internally -- no
  explicit duck_path wiring is needed here.  The scheduler also
  uses the shared gateway connection (no duck_path argument).
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from loguru import logger
import uvicorn

from optdash.config import settings
from optdash.api.app import create_app
from optdash.api.deps import startup, shutdown
from optdash.scheduler import create_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting OptDash API + Scheduler...")

    # 1. Open DB connections (DuckDB gateway + SQLite journal)
    await startup(app)

    # 2. Start scheduler (uses shared DuckDB gateway connection)
    scheduler = create_scheduler(
        journal_path=settings.JOURNAL_DB_PATH,
    )
    scheduler.start()
    app.state.scheduler = scheduler
    logger.info(
        "Scheduler started -- interval={}s, underlyings={}",
        settings.SCHEDULER_INTERVAL_SECONDS,
        settings.UNDERLYINGS,
    )

    yield

    # 3. Graceful shutdown
    logger.info("Shutting down OptDash...")
    try:
        scheduler.shutdown(wait=False)
    except Exception:
        pass
    await shutdown(app)


# Pass the full lifespan (with scheduler) into the app factory.
# This is the single source of truth for startup/teardown.
app = create_app(lifespan=lifespan)


if __name__ == "__main__":
    uvicorn.run(
        "run_api:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=False,
        log_level=settings.LOG_LEVEL.lower(),
    )
