"""Shared FastAPI dependencies — DuckDB + SQLite connection management.

DuckDB strategy:
  The in-process :memory: connection (with live Parquet views) is created by
  duckdb_gateway.startup() during the app lifespan — BEFORE this startup() runs.
  We reuse that same connection here so the API and the scheduler always read
  from the same live Parquet dataset rather than a separate stale .duckdb file.
"""
import sqlite3
from fastapi import FastAPI, Request
from loguru import logger

from optdash.config import settings
from optdash.ai.journal.schema import init_db
from optdash.pipeline.duckdb_gateway import get_conn as get_duck_conn


async def startup(app: FastAPI) -> None:
    """Wire the shared DuckDB gateway connection + open SQLite journal."""
    # Reuse the in-process :memory: connection created by duckdb_gateway.startup().
    # This ensures /market/* API endpoints read the same live Parquet views
    # that the scheduler analytics use — no separate stale .duckdb file.
    app.state.duck = get_duck_conn()
    logger.info("DuckDB API dependency wired to gateway in-memory connection.")

    logger.info("Connecting SQLite journal: {}", settings.JOURNAL_DB_PATH)
    jconn = sqlite3.connect(str(settings.JOURNAL_DB_PATH), check_same_thread=False)
    jconn.row_factory = sqlite3.Row

    # WAL mode: allows concurrent reads+writes without exclusive locking.
    # The scheduler writes position_snaps every 5 min while the API handles
    # accept/reject requests — both need simultaneous write access.
    jconn.execute("PRAGMA journal_mode=WAL")
    jconn.execute("PRAGMA synchronous=NORMAL")  # safe with WAL, ~3x faster writes
    jconn.execute("PRAGMA foreign_keys=ON")      # enforce referential integrity

    init_db(jconn)  # create tables + indexes (idempotent)
    app.state.journal = jconn
    logger.info("SQLite journal ready.")


async def shutdown(app: FastAPI) -> None:
    """Close SQLite journal. DuckDB lifecycle managed by duckdb_gateway.shutdown()."""
    try:
        app.state.journal.close()
    except Exception:
        pass


def get_duck(request: Request):
    return request.app.state.duck


def get_journal(request: Request) -> sqlite3.Connection:
    return request.app.state.journal
