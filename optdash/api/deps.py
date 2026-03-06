"""Shared FastAPI dependencies -- DuckDB + SQLite connection management.

DuckDB lifecycle
----------------
deps.startup() owns the FULL DuckDB lifecycle:
  1. Calls duckdb_gateway.startup() -- creates the in-process :memory:
     connection and registers the rolling processed/ Parquet view.
  2. Stores the returned connection on app.state.duck so all API routers
     receive it via Depends(get_duck).
  3. deps.shutdown() calls duckdb_gateway.shutdown() to close the
     connection cleanly before the process exits.

This means both the full-stack run (run_api.py) and the standalone DB-only
mode (uvicorn optdash.api.app:app) work correctly without any extra DuckDB
wiring in the entry points -- the lifespan just calls deps.startup/shutdown.
"""
import sqlite3
from fastapi import FastAPI, Request
from loguru import logger

from optdash.config import settings
from optdash.ai.journal.schema import init_db
from optdash.pipeline.duckdb_gateway import (
    startup  as duck_startup,
    shutdown as duck_shutdown,
    get_conn as get_duck_conn,  # noqa: F401  (kept for external callers)
)


async def startup(app: FastAPI) -> None:
    """Initialise DuckDB gateway and open SQLite journal."""
    # Start the in-process DuckDB connection and register the rolling
    # Parquet view.  Must happen first -- all API endpoints depend on it.
    duck_conn      = duck_startup()
    app.state.duck = duck_conn
    logger.info("DuckDB gateway initialised -- in-memory connection ready.")

    logger.info("Connecting SQLite journal: {}", settings.JOURNAL_DB_PATH)
    jconn = sqlite3.connect(str(settings.JOURNAL_DB_PATH), check_same_thread=False)
    jconn.row_factory = sqlite3.Row

    # WAL mode: allows concurrent reads+writes without exclusive locking.
    # The scheduler writes position_snaps every 5 min while the API handles
    # accept/reject requests -- both need simultaneous write access.
    jconn.execute("PRAGMA journal_mode=WAL")
    jconn.execute("PRAGMA synchronous=NORMAL")  # safe with WAL, ~3x faster writes
    jconn.execute("PRAGMA foreign_keys=ON")      # enforce referential integrity

    init_db(jconn)  # create tables + indexes (idempotent)
    app.state.journal = jconn
    logger.info("SQLite journal ready.")


async def shutdown(app: FastAPI) -> None:
    """Close DuckDB connection and SQLite journal."""
    duck_shutdown()
    try:
        app.state.journal.close()
    except Exception:
        pass


def get_duck(request: Request):
    return request.app.state.duck


def get_journal(request: Request) -> sqlite3.Connection:
    return request.app.state.journal
