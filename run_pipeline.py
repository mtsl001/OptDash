#!/usr/bin/env python
"""run_pipeline.py — Standalone BQ pipeline runner (no FastAPI required).

Usage
-----
  python run_pipeline.py              # backfill + gap fill + live loop
  python run_pipeline.py --once       # backfill + gap fill then exit
  python run_pipeline.py --gap-only   # gap fill only (skip backfill)

Safe to run alongside FastAPI
------------------------------
  writer.py uses FileLock + atomic rename on every Parquet write.
  watermark.py uses atomic .tmp + Path.replace() on every save.
  FileLock on data/pipeline.lock prevents two run_pipeline.py instances
  from running concurrently (e.g. accidental double-launch).

Live loop
----------
  Polls BQ every SCHEDULER_INTERVAL_SECONDS seconds.
  Only runs during NSE market hours (09:15–15:30 IST, trading days).
  Outside market hours the loop sleeps without querying BQ.
  Exits cleanly on KeyboardInterrupt or SIGTERM.
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

from filelock import FileLock, Timeout
from loguru import logger

from optdash.config import settings
from optdash.pipeline.market_calendar import is_within_market_hours

_LOCK_PATH = Path(settings.DATA_ROOT) / "pipeline.lock"
_running   = True   # set False by SIGTERM handler


def _sigterm_handler(signum, frame):
    global _running
    logger.info("run_pipeline: SIGTERM received — shutting down cleanly.")
    _running = False


signal.signal(signal.SIGTERM, _sigterm_handler)


def _run_once() -> None:
    """Backfill (if not skipped) + gap fill, then exit."""
    from optdash.pipeline.backfill import run_backfill
    from optdash.pipeline.gap_fill import run_gap_fill
    run_backfill(duck_conn=None)
    run_gap_fill(duck_conn=None)


def _run_gap_only() -> None:
    """Gap fill only (skip backfill), then exit."""
    from optdash.pipeline.gap_fill import run_gap_fill
    run_gap_fill(duck_conn=None)


def _live_loop() -> None:
    """Backfill + gap fill, then run live incremental pulls until stopped."""
    from optdash.pipeline.backfill import run_backfill
    from optdash.pipeline.gap_fill import run_gap_fill
    from optdash.pipeline.incremental import run_incremental_pull

    run_backfill(duck_conn=None)
    run_gap_fill(duck_conn=None)

    logger.info(
        "run_pipeline: entering live loop — interval={}s",
        settings.SCHEDULER_INTERVAL_SECONDS,
    )

    while _running:
        if is_within_market_hours():
            try:
                new_data = run_incremental_pull()
                if new_data:
                    logger.info("run_pipeline: incremental snap ingested.")
            except Exception as e:
                logger.error("run_pipeline: incremental pull failed: {}", e)
        else:
            logger.debug("run_pipeline: outside market hours — sleeping.")

        # Sleep in 1-second increments so SIGTERM is handled promptly.
        for _ in range(settings.SCHEDULER_INTERVAL_SECONDS):
            if not _running:
                break
            time.sleep(1)

    logger.info("run_pipeline: live loop exited cleanly.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OptDash BQ pipeline runner",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run backfill + gap fill then exit (no live loop)",
    )
    parser.add_argument(
        "--gap-only",
        action="store_true",
        help="Run gap fill only (skip backfill) then exit",
    )
    args = parser.parse_args()

    Path(settings.DATA_ROOT).mkdir(parents=True, exist_ok=True)

    try:
        with FileLock(str(_LOCK_PATH), timeout=5):
            if args.gap_only:
                _run_gap_only()
            elif args.once:
                _run_once()
            else:
                _live_loop()
    except Timeout:
        logger.error(
            "run_pipeline: another instance is already running (lock: {}). Exiting.",
            _LOCK_PATH,
        )
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("run_pipeline: KeyboardInterrupt — exiting cleanly.")


if __name__ == "__main__":
    main()
