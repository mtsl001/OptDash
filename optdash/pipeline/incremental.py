"""incremental.py — Live 5-min BQ pull during market hours.

Called by scheduler.py tick() Step 0 via asyncio.to_thread() so blocking
BQ I/O and Parquet write do not block the FastAPI event loop.

Returns True if new rows were written (caller may log snap ingested).
Returns False if no new data (normal between feed cadence windows).

No refresh_views() call: incremental writes update today's existing Parquet
file in-place (atomic rename). The DuckDB glob already covers this file.
refresh_views() is only needed when a NEW partition directory is created
(handled by processor._write_trade_date on first write for a new trade_date).
"""
from __future__ import annotations

from loguru import logger

from optdash.config import settings
from optdash.pipeline.bq_client import pull_incremental
from optdash.pipeline.processor import process_and_write
from optdash.pipeline.watermark import load as wm_load, save as wm_save


def run_incremental_pull() -> bool:
    """Pull rows since last watermark from upxtx and write to Parquet.

    Returns True if new rows were written, False if no new data.
    Non-fatal by design — caller (scheduler tick) catches any exception
    and continues with last available data.
    """
    wm = wm_load()
    df = pull_incremental(wm, settings.BQ_FQN_LIVE)

    if df is None or df.empty:
        return False

    # duck_conn=None: incremental always writes to an existing partition;
    # no new directory created, so no refresh_views() needed.
    new_wm = process_and_write(df, duck_conn=None)
    if new_wm and new_wm > wm:
        wm_save(new_wm)
        logger.debug("Incremental pull complete — watermark → {}", new_wm)
        return True

    return False
