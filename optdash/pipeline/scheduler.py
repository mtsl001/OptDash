"""APScheduler — 5-min market tick pipeline."""
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from optdash.config import settings
from optdash.pipeline.purge import purge_old_raw_parquets

IST = ZoneInfo("Asia/Kolkata")
_scheduler: BackgroundScheduler | None = None


def _is_market_hours(now: datetime) -> bool:
    """Return True if current IST time is within market hours."""
    from datetime import time
    t = now.time()
    open_h,  open_m  = map(int, settings.MARKET_OPEN.split(":"))
    close_h, close_m = map(int, settings.MARKET_CLOSE.split(":"))
    return time(open_h, open_m) <= t <= time(close_h, close_m)


def _run_tick(
    conn:  duckdb.DuckDBPyConnection,
    jconn: sqlite3.Connection,
) -> None:
    """Called every 5 min during market hours."""
    now = datetime.now(IST)
    if not _is_market_hours(now):
        return

    trade_date = now.strftime("%Y-%m-%d")
    snap_time  = now.strftime("%H:%M")

    logger.info("[Tick] {} {}", trade_date, snap_time)

    # Deferred imports to avoid circular deps at module load
    from optdash.ai.recommender    import generate_recommendation
    from optdash.ai.tracker        import track_open_positions, expire_stale_recommendations
    from optdash.ai.shadow_tracker import track_shadow_positions
    from optdash.ai.eod            import eod_force_close, finalize_all_shadows

    # Single source of truth — driven by config, no hardcoded list here.
    underlyings = settings.UNDERLYINGS

    # Step 1: Track all open positions
    try:
        track_open_positions(conn, jconn, trade_date, snap_time)
    except Exception as e:
        logger.error("track_open_positions failed: {}", e)

    # Step 2: Track shadow positions
    try:
        track_shadow_positions(conn, jconn, trade_date, snap_time)
    except Exception as e:
        logger.error("track_shadow_positions failed: {}", e)

    # Step 3: Expire stale recommendations
    try:
        expire_stale_recommendations(jconn, trade_date, snap_time)
    except Exception as e:
        logger.error("expire_stale_recommendations failed: {}", e)

    # Step 4: Generate new recommendations
    for underlying in underlyings:
        try:
            rec = generate_recommendation(conn, jconn, trade_date, snap_time, underlying)
            if rec:
                logger.info(
                    "[AI] Recommendation: {} {} {} @ {}",
                    underlying, rec["option_type"],
                    rec["strike_price"],  rec["entry_premium"],
                )
        except Exception as e:
            logger.error("generate_recommendation failed for {}: {}", underlying, e)

    # Step 5: EOD sweeps — fire exactly ONCE at the designated snap.
    # Using == (not >=) so these don't repeat on every subsequent tick,
    # which would attempt to re-close already-CLOSED trades/shadows.
    if snap_time == settings.EOD_FORCE_CLOSE_TIME:
        try:
            eod_force_close(conn, jconn, trade_date)
        except Exception as e:
            logger.error("eod_force_close failed: {}", e)

    if snap_time == settings.EOD_SWEEP_TIME:
        try:
            finalize_all_shadows(conn, jconn, trade_date)
        except Exception as e:
            logger.error("finalize_all_shadows failed: {}", e)

        # PIPELINE-004: Purge raw Parquet files outside the retention window.
        # Runs once per day at EOD sweep time after shadows are finalized.
        try:
            purge_old_raw_parquets(
                Path(settings.DATA_ROOT),
                settings.RAW_PARQUET_RETENTION_DAYS,
            )
        except Exception as e:
            logger.error("raw Parquet purge failed: {}", e)


def start_scheduler(
    conn:  duckdb.DuckDBPyConnection,
    jconn: sqlite3.Connection,
) -> BackgroundScheduler:
    """Start the APScheduler background scheduler."""
    global _scheduler
    _scheduler = BackgroundScheduler(timezone=IST)
    _scheduler.add_job(
        func=lambda: _run_tick(conn, jconn),
        trigger=CronTrigger(
            minute="*/5",
            hour="9-15",
            day_of_week="mon-fri",
            timezone=IST,
        ),
        id="market_tick",
        name="5-min Market Tick",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info("Scheduler started — 5-min tick active")
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
