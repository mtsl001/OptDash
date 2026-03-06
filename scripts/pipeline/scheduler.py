"""
scheduler.py — APScheduler wrapper for the 5-minute incremental pull.

Uses BlockingScheduler (blocks the main thread — run in its own process).
Fires the pipeline function every 5 minutes from 09:16:30 until market close.
Market-hours check inside the job function handles weekend/holiday skips.
"""
import logging
from datetime import datetime
from typing import Callable

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from market_calendar import is_within_market_hours, next_scheduled_run

logger = logging.getLogger(__name__)


def _job_wrapper(pipeline_fn: Callable) -> None:
    """
    Wrapper executed by APScheduler every 5 minutes.
    1. Checks market hours — skips silently if closed (weekend, holiday, after 15:30).
    2. Calls pipeline_fn() → BQ pull → validate → process → write → watermark.
    3. Catches all exceptions — scheduler MUST keep running even if one pull fails.
    """
    now = datetime.now()
    if not is_within_market_hours(now):
        logger.debug(f"[{now.strftime('%H:%M:%S')}] Market closed — skipping pull")
        return

    logger.info(f"[{now.strftime('%H:%M:%S')}] ── Incremental pull starting ──")
    try:
        pipeline_fn()
        logger.info(f"[{now.strftime('%H:%M:%S')}] ── Pull complete ──")
    except Exception as exc:
        logger.error(
            f"[{now.strftime('%H:%M:%S')}] Pull job raised exception: {exc}",
            exc_info=True,
        )
        # Never re-raise — scheduler continues unaffected


def start_live_scheduler(pipeline_fn: Callable) -> None:
    """
    Start the blocking APScheduler.
    Fires pipeline_fn every 5 minutes from the next scheduled slot.
    Blocks until Ctrl+C or SystemExit.

    Call this AFTER backfill and gap-fill are complete.
    """
    start_dt = next_scheduled_run()
    if start_dt is None:
        logger.warning(
            "Market is closed or past 15:30 — live scheduler not started. "
            "Backfill and gap-fill completed successfully. Re-run tomorrow morning."
        )
        return

    scheduler = BlockingScheduler()
    scheduler.add_job(
        func=_job_wrapper,
        args=[pipeline_fn],
        trigger=IntervalTrigger(
            minutes=5,
            start_date=start_dt,
        ),
        id="incremental_pull",
        name="5-min BQ incremental pull",
        max_instances=1,        # Never overlap runs
        misfire_grace_time=45,  # Allow 45-sec late start before treating as missed
        coalesce=True,          # If multiple misfires stack up, run only once
    )

    logger.info(
        f"Live scheduler started. "
        f"First pull: {start_dt.strftime('%H:%M:%S')} IST. "
        f"Interval: 5 minutes. "
        f"Market close: 15:30."
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped cleanly")
        scheduler.shutdown(wait=False)
