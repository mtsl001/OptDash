"""Shared FastAPI dependencies — DuckDB + SQLite connection management."""
import sqlite3
import duckdb
from fastapi import FastAPI, Request
from loguru import logger

from optdash.config import settings
from optdash.ai.journal.schema import init_db


async def startup(app: FastAPI) -> None:
    """Open DuckDB (read-only) and SQLite journal connections."""
    logger.info("Connecting DuckDB: {}", settings.DUCKDB_PATH)
    app.state.duck = duckdb.connect(str(settings.DUCKDB_PATH), read_only=True)

    logger.info("Connecting SQLite journal: {}", settings.JOURNAL_DB_PATH)
    jconn = sqlite3.connect(str(settings.JOURNAL_DB_PATH), check_same_thread=False)
    jconn.row_factory = sqlite3.Row
    init_db(jconn)
    app.state.journal = jconn
    logger.info("Database connections ready.")


async def shutdown(app: FastAPI) -> None:
    try:
        app.state.duck.close()
    except Exception:
        pass
    try:
        app.state.journal.close()
    except Exception:
        pass


def get_duck(request: Request) -> duckdb.DuckDBPyConnection:
    return request.app.state.duck


def get_journal(request: Request) -> sqlite3.Connection:
    return request.app.state.journal
