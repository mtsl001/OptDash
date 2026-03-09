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

SQLite lifecycle
----------------
deps.startup() opens TWO SQLite connections to the same WAL-mode database:

  app.state.journal            -- used by API request handlers.  FastAPI
                                  runs sync (def) endpoints in anyio's
                                  thread pool via anyio.to_thread.run_sync(),
                                  so this connection lives in a worker thread.

  app.state.scheduler_journal  -- used exclusively by the APScheduler tick,
                                  which is a coroutine running in the asyncio
                                  event loop thread.

Two separate connections are required because Python's sqlite3.Connection is
NOT thread-safe.  check_same_thread=False only disables the safety check; it
does not add actual thread-safety.  Sharing one Connection across threads can
silently corrupt its internal state.

SQLite's WAL mode coordinates concurrent writes between the two connections at
the file level safely -- this is exactly the use-case WAL was designed for.

Ordering guarantee (P2-F10)
---------------------------
app.state.scheduler_journal is opened AFTER init_db(jconn) returns.
SQLite WAL mode guarantees that any connection opened after a commit
immediately sees the committed schema.  This ensures the scheduler
cannot write against a partially-migrated schema on fast startup.

Prerequisite: _run_migrations() must propagate real errors (not swallow
them with bare except: pass) -- fixed in P1-F13 / schema.py.  Without
that fix, init_db() can return early with an incomplete schema and
this ordering guarantee is vacuous.

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


def _open_journal_conn(path: str) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and FK enforcement.

    Extracted from startup() so both the API connection and the scheduler
    connection are configured identically without duplicating the PRAGMA block.
    """
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL mode: allows concurrent reads+writes without exclusive locking.
    # The scheduler writes position_snaps every 5 min while the API handles
    # accept/reject requests -- both need simultaneous write access.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")  # safe with WAL, ~3x faster writes
    conn.execute("PRAGMA foreign_keys=ON")      # enforce referential integrity
    return conn


async def startup(app: FastAPI) -> None:
    """Initialise DuckDB gateway and open two SQLite journal connections."""
    # Start the in-process DuckDB connection and register the rolling
    # Parquet view.  Must happen first -- all API endpoints depend on it.
    duck_conn      = duck_startup()
    app.state.duck = duck_conn
    logger.info("DuckDB gateway initialised -- in-memory connection ready.")

    logger.info("Connecting SQLite journal: {}", settings.JOURNAL_DB_PATH)

    # Connection 1: API layer -- used by FastAPI sync endpoints
    # (anyio thread pool, separate OS thread from the event loop).
    jconn = _open_journal_conn(settings.JOURNAL_DB_PATH)
    init_db(jconn)               # create tables + indexes + all migrations (idempotent)
    app.state.journal = jconn

    # Connection 2: Scheduler -- opened AFTER init_db() fully returns.
    # WAL mode guarantees this connection sees the fully committed schema
    # immediately (P2-F10 ordering guarantee -- see module docstring).
    sched_conn = _open_journal_conn(settings.JOURNAL_DB_PATH)
    app.state.scheduler_journal = sched_conn

    logger.info(
        "SQLite journal ready -- 2 connections opened "
        "(API thread-pool conn + scheduler event-loop conn)."
    )


async def shutdown(app: FastAPI) -> None:
    """Close DuckDB connection and both SQLite journal connections."""
    duck_shutdown()
    for attr in ("journal", "scheduler_journal"):
        # Use getattr with a default of None so a never-set attribute
        # (e.g. startup crashed before _open_journal_conn) doesn't raise
        # AttributeError and mask the original startup failure in the log.
        conn = getattr(app.state, attr, None)
        if conn is not None:
            try:
                conn.close()
            except Exception as e:
                logger.warning("Error closing SQLite connection '{}': {}", attr, e)


def get_duck(request: Request):
    return request.app.state.duck


def get_journal(request: Request) -> sqlite3.Connection:
    return request.app.state.journal
