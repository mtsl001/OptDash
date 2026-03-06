"""
run_pipeline.py — Main entry point for the OptDash data pipeline.

Startup sequence
----------------
  1. Setup logging
  2. Ensure data/processed/ and data/processed/atm_windows/ exist
  3. Run gap_fill  — recover any missed intraday windows from prior sessions
  4. Run backfill  — pull any historical days absent from Parquet
  5. Start live scheduler — 5-minute incremental BQ pulls until 15:30

Incremental pull cycle (step 5, repeated every 5 min)
------------------------------------------------------
  1. Load watermark
  2. Pull incremental rows from BigQuery (record_time > watermark)
  3. Validate schema and data quality
  4. Compute or load ATM windows for today
  5. Compute derived columns (all 7 processor fixes applied)
  6. Write to data/processed/trade_date=YYYY-MM-DD/UNDERLYING.parquet
  7. Update watermark (only after successful write)

Usage
-----
  cd <repo_root>
  python -m scripts.pipeline.run_pipeline

  # OR run directly from scripts/pipeline/:
  cd scripts/pipeline
  python run_pipeline.py

Environment variables required
-------------------------------
  BQ_TABLE_FQN               e.g. "project.dataset.options_snaps"
  GOOGLE_APPLICATION_CREDENTIALS  path to service account JSON

Optional
--------
  BQ_PROJECT                 defaults to first segment of BQ_TABLE_FQN
  BACKFILL_START_DATE        ISO date, default "2026-01-01"
  BACKFILL_END_DATE          ISO date, default "2026-03-05"
"""
import logging
import sys
from datetime import date
from pathlib import Path

from google.cloud import bigquery

# ── Bootstrap: add scripts/pipeline to sys.path when run directly ────────────
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from logger import setup_logging
from config import (
    PROCESSED_DIR,
    ATM_WINDOWS_DIR,
    WATERMARK_PATH,
)
from bq_client import get_bq_client, pull_incremental
from validator import validate_dataframe
from atm import compute_atm_windows, save_atm_windows, load_atm_windows
from processor import compute_derived_columns
from writer import write_incremental_parquet
from duckdb_setup import safe_refresh_views
import watermark as wm
from gap_fill import run_gap_fill, gap_fill_status
from backfill import run_backfill
from scheduler import start_live_scheduler

logger = logging.getLogger(__name__)

# Module-level BQ client — created once at startup, reused every 5-min cycle
_bq_client: bigquery.Client | None = None


def _get_client() -> bigquery.Client:
    global _bq_client
    if _bq_client is None:
        _bq_client = get_bq_client()
    return _bq_client


def run_incremental_pull() -> None:
    """
    Execute one full incremental pull cycle.
    Called by the APScheduler every 5 minutes during market hours.

    Steps:
      1. Load watermark
      2. BQ pull (record_time > watermark)
      3. Validate
      4. ATM windows (load if exists, else compute + save)
      5. Derived columns
      6. Write to data/processed/trade_date=DS/UNDERLYING.parquet
      7. Update watermark (only on success)
    """
    client = _get_client()

    # Step 1: Load watermark
    current_wm = wm.load(WATERMARK_PATH)
    logger.info(f"Watermark: {current_wm}")

    # Step 2: Pull incremental rows
    df = pull_incremental(client, current_wm)
    if df.empty:
        logger.debug("No new rows — snapshot not yet committed to BQ")
        return

    # Step 3: Validate
    df = validate_dataframe(df)

    # Step 4: ATM windows
    today = date.today()
    atm_windows = load_atm_windows(today, ATM_WINDOWS_DIR)
    if not atm_windows:
        logger.info(f"Computing ATM windows for {today}")
        atm_windows = compute_atm_windows(df, today)
        if atm_windows:
            save_atm_windows(atm_windows, today, ATM_WINDOWS_DIR)
        else:
            logger.warning("Could not compute ATM windows — using full strike universe")

    # Step 5: Derived columns (all 7 processor fixes applied)
    df = compute_derived_columns(df, atm_windows)

    # Step 6: Write to data/processed/trade_date=DS/UNDERLYING.parquet
    write_incremental_parquet(df, PROCESSED_DIR)

    # No-op shim — DuckDB views managed by API process
    safe_refresh_views()

    # Step 7: Update watermark — ONLY after successful write
    max_ts = df["record_time"].max()
    new_wm = wm.from_timestamp(max_ts)
    wm.save(WATERMARK_PATH, new_wm)

    n_snaps = df["snap_time"].nunique()
    snaps   = sorted(df["snap_time"].unique())
    logger.info(
        f"Incremental complete: {len(df):,} rows, "
        f"{n_snaps} snap(s) [{', '.join(snaps)}], "
        f"watermark → {new_wm}"
    )


def _ensure_dirs() -> None:
    """Create required directories if they don't exist."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    ATM_WINDOWS_DIR.mkdir(parents=True, exist_ok=True)
    WATERMARK_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Data dirs confirmed: {PROCESSED_DIR}")


def main() -> None:
    """
    Full startup sequence:
      1. Setup logging
      2. Ensure directories exist
      3. Gap fill   — recover missed intraday windows
      4. Backfill   — pull any missing historical days
      5. Live scheduler — 5-min incremental pulls until market close
    """
    # Step 1: Logging (console + rotating file in logs/pipeline.log)
    setup_logging(log_dir=Path("logs"))
    logger.info("=" * 60)
    logger.info("OptDash Pipeline starting up")
    logger.info("=" * 60)

    # Step 2: Directories
    _ensure_dirs()

    # Step 3: Gap fill — recover any missed intraday windows
    try:
        status = gap_fill_status()
        if status["gaps_found"] > 0:
            logger.info(
                f"Gap fill: {status['gaps_found']} gap(s) detected — "
                f"recovering before scheduler starts"
            )
            for g in status["gaps"]:
                logger.info(
                    f"  Gap: {g['date']} from {g['from']} "
                    f"(~{g['gap_minutes']} min missing)"
                )
        run_gap_fill()
    except Exception as exc:
        logger.error(f"Gap fill failed: {exc}", exc_info=True)
        logger.warning("Continuing without gap fill — gaps will persist")

    # Step 4: Backfill — pull any historical days absent from disk
    try:
        run_backfill()
    except Exception as exc:
        logger.error(f"Backfill failed: {exc}", exc_info=True)
        logger.warning("Continuing without backfill")

    # Step 5: Live scheduler — blocks until Ctrl+C or market close
    logger.info("Starting live 5-minute incremental scheduler…")
    start_live_scheduler(run_incremental_pull)

    logger.info("Pipeline shut down cleanly")


if __name__ == "__main__":
    main()
