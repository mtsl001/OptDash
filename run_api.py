"""Entry point — starts FastAPI + scheduler."""
import asyncio
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
    await startup(app)

    scheduler = create_scheduler(
        duck_path=settings.DUCKDB_PATH,
        journal_path=settings.JOURNAL_DB_PATH,
    )
    scheduler.start()
    app.state.scheduler = scheduler
    logger.info("Scheduler started (interval={}s)", settings.SCHEDULER_INTERVAL_SECONDS)

    yield

    logger.info("Shutting down...")
    try:
        scheduler.shutdown(wait=False)
    except Exception:
        pass
    await shutdown(app)


app = create_app()
app.router.lifespan_context = lifespan


if __name__ == "__main__":
    uvicorn.run(
        "run_api:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=False,
        log_level="info",
    )
