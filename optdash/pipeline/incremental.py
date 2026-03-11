"""incremental.py -- Live 5-min BQ pull during market hours.

Called by scheduler.py tick() Step 0 via asyncio.to_thread() so blocking
BQ I/O and Parquet write do not block the FastAPI event loop.

Returns True if new rows were written (caller may log snap ingested).
Returns False if no new data (normal between feed cadence windows).

P1-P2-8: duck_conn is now accepted and forwarded to process_and_write().
When the first tick of a new trading day creates a new partition directory,
processor._write_trade_date() calls refresh_views(duck_conn) immediately so
the new day is visible to DuckDB analytics on the very first tick (09:15)
rather than waiting until the EOD refresh_views() at 15:25.

The scheduler passes get_conn() as duck_conn; non-scheduler callers (tests,
cli scripts) may pass None (default) to skip the refresh_views call.
"""
from __future__ import annotations

from loguru import logger

from optdash.config import settings
from optdash.pipeline.bq_client import pull_incremental
from optdash.pipeline.processor import process_and_write
from optdash.pipeline.watermark import load as wm_load, save as wm_save


def run_incremental_pull(duck_conn=None) -> bool:
    """Pull rows since last watermark from upxtx and write to Parquet.

    Parameters
    ----------
    duck_conn : LockedConn | None
        Live DuckDB connection.  When provided and a new partition directory
        is created (first tick of a new trading day), refresh_views() is
        called inside process_and_write -> _write_trade_date so the new day
        is immediately queryable.  Pass None (default) to skip the refresh
        (safe for tests and non-scheduler callers where no live DuckDB
        connection exists).

    Returns True if new rows were written, False if no new data.
    Non-fatal by design -- caller (scheduler tick) catches any exception
    and continues with last available data.
    """
    wm = wm_load()
    df = pull_incremental(wm, settings.BQ_FQN_LIVE)

    if df is None or df.empty:
        return False

    # P1-P2-8: forward duck_conn so _write_trade_date() can call
    # refresh_views() when a new partition directory is created on the first
    # tick of a new trading day.  Without this, the new day's Parquet is
    # written to disk but invisible to DuckDB until the EOD refresh at 15:25.
    new_wm = process_and_write(df, duck_conn=duck_conn)
    if new_wm and new_wm > wm:
        wm_save(new_wm)
        logger.debug("Incremental pull complete -- watermark -> {}", new_wm)
        return True

    return False
