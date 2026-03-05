"""APScheduler — drives recommendation generation and position tracking.

Tick structure (every SCHEDULER_INTERVAL_SECONDS, market hours only):
  1. Expire stale pending recommendations  → once per tick (all underlyings)
  2. Generate recommendations              → once per underlying
  3. Track open positions                  → once per tick (all open trades)
  4. Track shadow positions                → once per tick (all shadow trades)
  5. EOD sweep                             → once per calendar day
"""
import duckdb
import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from optdash.config import settings
from optdash.ai.recommender import generate_recommendation
from optdash.ai.tracker import track_open_positions, expire_stale_recommendations
from optdash.ai.shadow_tracker import track_shadow_positions
from optdash.ai.eod import eod_force_close, finalize_all_shadows
from optdash.pipeline.purge import purge_old_raw_parquets

IST = ZoneInfo("Asia/Kolkata")


def _now_ist() -> datetime:
    return datetime.now(tz=IST)


def _today_str() -> str:
    return date.today().strftime("%Y-%m-%d")


def _snap_time_str() -> str:
    """Round down to nearest 5-min snap (HH:MM)."""
    now  = _now_ist()
    mins = (now.minute // 5) * 5
    return f"{now.hour:02d}:{mins:02d}"


def _is_market_hours() -> bool:
    now = _now_ist()
    if now.weekday() >= 5:          # Sat / Sun
        return False
    t = now.hour * 60 + now.minute
    return (9 * 60 + 15) <= t <= (15 * 60 + 30)


def _is_eod() -> bool:
    return _snap_time_str() >= settings.EOD_SWEEP_TIME


def _eod_done_today(done_flags: dict) -> bool:
    return done_flags.get(_today_str(), False)


def _make_jconn(journal_path) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and FK enforcement."""
    jconn = sqlite3.connect(str(journal_path), check_same_thread=False)
    jconn.row_factory = sqlite3.Row
    jconn.execute("PRAGMA journal_mode=WAL")
    jconn.execute("PRAGMA synchronous=NORMAL")  # safe with WAL, ~3x faster
    jconn.execute("PRAGMA foreign_keys=ON")
    return jconn


def create_scheduler(
    duck_path:    str,
    journal_path: str,
) -> AsyncIOScheduler:
    """
    Returns a configured AsyncIOScheduler.
    Call scheduler.start() inside FastAPI lifespan (after startup()).
    """
    done_flags: dict[str, bool] = {}

    async def tick() -> None:
        if not _is_market_hours():
            return

        trade_date = _today_str()
        snap_time  = _snap_time_str()

        # Guard uninitialized variables so finally block is always safe
        duck  = None
        jconn = None

        try:
            duck  = duckdb.connect(str(duck_path), read_only=True)
            jconn = _make_jconn(journal_path)
            # Tables are guaranteed to exist: init_db() ran in startup() before
            # the scheduler was started (see run_api.py lifespan order).

            # ── EOD sweep (once per calendar day) ─────────────────────────────
            if _is_eod() and not _eod_done_today(done_flags):
                logger.info("Running EOD sweep for {}", trade_date)
                eod_force_close(duck, jconn, trade_date)
                finalize_all_shadows(duck, jconn, trade_date)

                # PIPELINE-004: purge stale raw Parquets (once per day at EOD).
                try:
                    purge_old_raw_parquets(
                        Path(settings.DATA_ROOT),
                        settings.RAW_PARQUET_RETENTION_DAYS,
                    )
                except Exception as purge_err:
                    logger.error("raw Parquet purge failed: {}", purge_err)

                done_flags[trade_date] = True
                return  # skip normal tick on EOD

            # ── Step 1: Expire stale pending recommendations (all underlyings) ──
            expire_stale_recommendations(jconn, trade_date, snap_time)

            # ── Step 2: Generate new recommendations (per underlying) ─────────
            for underlying in settings.UNDERLYINGS:
                rec = generate_recommendation(
                    duck, jconn, trade_date, snap_time, underlying
                )
                if rec:
                    logger.info(
                        "[{}] Recommendation issued: {} {} @ {}",
                        snap_time,
                        underlying,
                        rec.get("option_type"),
                        rec.get("strike_price"),
                    )

            # ── Step 3: Track all open positions (once, not per-underlying) ────
            track_open_positions(duck, jconn, trade_date, snap_time)

            # ── Step 4: Track all shadow positions (once) ─────────────────
            track_shadow_positions(duck, jconn, trade_date, snap_time)

        except Exception as e:
            logger.error("Scheduler tick error @ {}: {}", snap_time, e)
        finally:
            if duck:
                try:
                    duck.close()
                except Exception:
                    pass
            if jconn:
                try:
                    jconn.close()
                except Exception:
                    pass

    scheduler = AsyncIOScheduler(timezone=IST)
    scheduler.add_job(
        tick,
        trigger="interval",
        seconds=settings.SCHEDULER_INTERVAL_SECONDS,
        id="main_tick",
        max_instances=1,   # never overlap ticks
        coalesce=True,     # skip missed ticks on wake-up
    )
    return scheduler
