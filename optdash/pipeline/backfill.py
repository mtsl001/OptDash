"""backfill.py — Pull full historical days from upxtx_ar, write to processed/.

Idempotent: skips any day where processed/trade_date=.../NIFTY.parquet
already exists (NIFTY is the sentinel underlying).

Fatal on per-day failure: does not silently continue. Any BQ pull or
processor error raises so the operator is alerted immediately rather than
the app starting with a silent gap in the historical dataset.

Entry points:
  run_backfill(duck_conn)             — full range (BACKFILL_START_DATE to yesterday)
  run_backfill_one_day(date, duck_conn) — force re-process one day (ignores sentinel)
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from loguru import logger

from optdash.config import settings
from optdash.pipeline.bq_client import pull_full_day
from optdash.pipeline.market_calendar import get_trading_days, yesterday_ist
from optdash.pipeline.processor import process_and_write
from optdash.pipeline.watermark import load as wm_load, save as wm_save
from optdash.pipeline.writer import parquet_path


def _day_complete(trade_date_str: str) -> bool:
    """True if NIFTY.parquet exists for this trade_date (sentinel underlying).

    NIFTY is always present on every trading day and is the first underlying
    processed, making it a reliable sentinel for day-level completion.
    """
    return parquet_path(
        Path(settings.DATA_ROOT), trade_date_str, "NIFTY"
    ).exists()


def run_backfill(duck_conn=None) -> None:
    """Pull and process all outstanding historical days from upxtx_ar.

    Skips days already processed (idempotent). Raises on any per-day
    failure so the operator is alerted before the app enters market hours
    with a gap in historical data.
    """
    if not settings.ENABLE_BACKFILL:
        logger.info("Backfill disabled (ENABLE_BACKFILL=false) — skipping")
        return

    end_str  = settings.BACKFILL_END_DATE.strip()
    end_date = date.fromisoformat(end_str) if end_str else yesterday_ist()
    start_date = date.fromisoformat(settings.BACKFILL_START_DATE)

    if start_date > end_date:
        logger.info("Backfill range empty ({} > {}) — nothing to do", start_date, end_date)
        return

    days    = get_trading_days(start_date, end_date)
    pending = [d for d in days if not _day_complete(d.strftime("%Y-%m-%d"))]

    if not pending:
        logger.info(
            "Backfill: all {} trading days already present ({})",
            len(days), settings.BACKFILL_START_DATE,
        )
        return

    logger.info(
        "Backfill: {} of {} days pending ({}\u2013{})",
        len(pending), len(days), pending[0], pending[-1],
    )

    current_wm = wm_load()

    for d in pending:
        ds = d.strftime("%Y-%m-%d")
        try:
            df = pull_full_day(ds, settings.BQ_FQN_ARCHIVE)
            if df.empty:
                logger.warning("Backfill: {} — 0 rows from BQ, skipping", ds)
                continue
            new_wm = process_and_write(df, duck_conn=duck_conn)
            if new_wm and new_wm > current_wm:
                wm_save(new_wm)
                current_wm = new_wm
            logger.info("Backfill: {} complete", ds)
        except Exception as e:
            logger.error("Backfill: FAILED for {} — {}", ds, e)
            raise   # fatal: operator must investigate before app enters market hours


def run_backfill_one_day(trade_date_str: str, duck_conn=None) -> None:
    """Force re-process one specific day (ignores _day_complete sentinel).

    Useful for re-ingesting a day after a BQ feed correction or schema change.
    """
    logger.info("run_backfill_one_day: forcing re-process of {}", trade_date_str)
    df = pull_full_day(trade_date_str, settings.BQ_FQN_ARCHIVE)
    if df.empty:
        logger.warning("run_backfill_one_day: {} — 0 rows from BQ", trade_date_str)
        return
    new_wm = process_and_write(df, duck_conn=duck_conn)
    if new_wm:
        wm_save(new_wm)
    logger.info("run_backfill_one_day: {} complete", trade_date_str)
