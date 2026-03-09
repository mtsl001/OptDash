"""Entry point -- starts FastAPI + APScheduler.

Usage:  python run_api.py

Lifespan here owns both DB connections (via deps.startup/shutdown)
and the scheduler.  The create_app() factory receives this lifespan
so there is no double-initialisation.

DuckDB note:
  deps.startup() calls duckdb_gateway.startup() internally -- no
  explicit duck_path wiring is needed here.  The scheduler also
  uses the shared gateway connection (no duck_path argument).

SQLite note:
  deps.startup() opens TWO SQLite connections:
    app.state.journal            -- API request handlers (anyio thread pool)
    app.state.scheduler_journal  -- APScheduler tick (asyncio event loop)
  The scheduler receives its own dedicated connection so it never shares
  a sqlite3.Connection object with the API thread pool (thread-safety fix).
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

    # 1. Open DB connections:
    #    - DuckDB gateway (shared, event-loop safe)
    #    - SQLite API connection    -> app.state.journal
    #    - SQLite scheduler conn    -> app.state.scheduler_journal
    await startup(app)

    # 2. Start scheduler inside try/finally so DB connections are ALWAYS
    #    closed, even if scheduler.start() raises (e.g. port conflict,
    #    bad APScheduler config, import error in the tick function).
    #    Previously, any exception here left DuckDB + both SQLite connections
    #    open permanently because the code after `yield` was never reached.
    scheduler = None
    try:
        scheduler = create_scheduler(
            journal_conn=app.state.scheduler_journal,
        )
        scheduler.start()
        app.state.scheduler = scheduler
        logger.info(
            "Scheduler started -- interval={}s, underlyings={}",
            settings.SCHEDULER_INTERVAL_SECONDS,
            settings.UNDERLYINGS,
        )

        yield

    finally:
        # 3. Graceful shutdown -- guaranteed to run regardless of how we exit
        #    (normal return, exception from scheduler.start(), or SIGINT).
        logger.info("Shutting down OptDash...")
        if scheduler is not None:
            # scheduler is not None only if create_scheduler() succeeded AND
            # scheduler.start() did not raise -- safe to shut down.
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
