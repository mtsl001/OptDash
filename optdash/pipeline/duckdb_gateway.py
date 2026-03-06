"""DuckDB gateway -- in-process connection over processed Parquet files.

View scope
----------
Reads ONLY ``data/processed/trade_date=*/`` -- never ``data/raw/``.
The raw subtree has a different schema (no Greeks, no enriched columns)
and including it via union_by_name produces NULL rows for every
analytic column (delta, iv, gex, s_score, ...).

File layout expected
--------------------
  data/processed/trade_date=YYYY-MM-DD/
      NIFTY.parquet       <- all snaps for NIFTY on that day
      BANKNIFTY.parquet
      ...

DuckDB extracts ``trade_date`` automatically from the hive partition
directory name.  The ``underlying`` column is embedded in each file by
the writer (optdash/pipeline/writer.py).

Rolling window
--------------
On startup (and on demand via refresh_views), the view is registered
over only the last DUCK_VIEW_LOOKBACK_DAYS calendar days.  This bounds
the number of files DuckDB opens regardless of how long the service has
been running.

View refresh
------------
Call refresh_views(conn) at day rollover so new-day partition directories
become visible without restarting the process.  The scheduler calls this
once per day during the EOD sweep block.
"""
import duckdb
from datetime import date, timedelta
from pathlib import Path
from loguru import logger

from optdash.config import settings

_conn: duckdb.DuckDBPyConnection | None = None

PROCESSED_SUBDIR = "processed"


def startup() -> duckdb.DuckDBPyConnection:
    """Create in-process DuckDB connection and register rolling Parquet view.

    Returns the connection so callers (deps.startup) can store it on
    app.state.duck without a second call to get_conn().
    """
    global _conn
    _conn = duckdb.connect(database=":memory:", read_only=False)
    _conn.execute("PRAGMA threads=4")
    _conn.execute("PRAGMA memory_limit='2GB'")
    refresh_views(_conn)
    logger.info("DuckDB gateway started -- data root: {}", settings.DATA_ROOT)
    return _conn


def refresh_views(conn: duckdb.DuckDBPyConnection) -> None:
    """Register (or re-register) the rolling window options_data view.

    Uses CREATE OR REPLACE VIEW so it is safe to call at any time.
    Call once per trading day at EOD so the new-day partition directory
    enters the rolling window without requiring a process restart.
    """
    data_root = Path(settings.DATA_ROOT)
    processed = data_root / PROCESSED_SUBDIR

    if not data_root.exists():
        logger.warning(
            "DATA_ROOT does not exist: {} -- view not registered", data_root
        )
        return

    globs = _build_rolling_globs(processed, settings.DUCK_VIEW_LOOKBACK_DAYS)
    if not globs:
        logger.warning(
            "No processed Parquet directories found under {} -- view not registered",
            processed,
        )
        return

    try:
        conn.execute(
            "CREATE OR REPLACE VIEW options_data AS "
            "SELECT * FROM read_parquet($1, hive_partitioning=true, union_by_name=true)",
            [globs],
        )
        logger.info(
            "options_data view registered -- {} day partition(s) in rolling window",
            len(globs),
        )
    except Exception as e:
        logger.error("Failed to register Parquet view: {}", e)


def _build_rolling_globs(processed_root: Path, lookback_days: int) -> list[str]:
    """Return per-day *.parquet glob strings for the rolling lookback window.

    Only includes date directories that actually exist on disk -- the
    view registration never fails on a fresh install with no data.
    """
    today = date.today()
    globs: list[str] = []
    for i in range(lookback_days):
        d       = today - timedelta(days=i)
        day_dir = processed_root / f"trade_date={d.strftime('%Y-%m-%d')}"
        if day_dir.exists():
            globs.append(str(day_dir / "*.parquet"))
    return globs


def get_conn() -> duckdb.DuckDBPyConnection:
    if _conn is None:
        raise RuntimeError("DuckDB not initialized. Call startup() first.")
    return _conn


def shutdown() -> None:
    global _conn
    if _conn:
        _conn.close()
        _conn = None
        logger.info("DuckDB gateway shutdown")
