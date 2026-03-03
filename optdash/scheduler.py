"""APScheduler — drives recommendation generation and position tracking."""
import duckdb
import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from optdash.config import settings
from optdash.ai.recommender import generate_recommendation
from optdash.ai.tracker import track_open_positions, expire_stale_recommendations
from optdash.ai.shadow_tracker import track_shadow_positions
from optdash.ai.eod import eod_force_close, finalize_all_shadows
from optdash.ai.journal.schema import init_db

IST = ZoneInfo("Asia/Kolkata")


def _now_ist() -> datetime:
    return datetime.now(tz=IST)


def _today_str() -> str:
    return date.today().strftime("%Y-%m-%d")


def _snap_time_str() -> str:
    """Round down to nearest 5-min snap."""
    now = _now_ist()
    mins = (now.minute // 5) * 5
    return f"{now.hour:02d}:{mins:02d}"


def _is_market_hours() -> bool:
    now = _now_ist()
    # Mon-Fri, 09:15 to 15:30
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return (9 * 60 + 15) <= t <= (15 * 60 + 30)


def _is_eod() -> bool:
    now  = _now_ist()
    snap = _snap_time_str()
    return snap >= settings.EOD_SWEEP_TIME


def _eod_done_today(done_flags: dict) -> bool:
    return done_flags.get(_today_str(), False)


def create_scheduler(
    duck_path:    str,
    journal_path: str,
) -> AsyncIOScheduler:
    """
    Returns a configured AsyncIOScheduler.
    Call scheduler.start() inside FastAPI lifespan.
    """
    done_flags: dict[str, bool] = {}

    async def tick() -> None:
        if not _is_market_hours():
            return

        trade_date = _today_str()
        snap_time  = _snap_time_str()

        try:
            duck   = duckdb.connect(str(duck_path), read_only=True)
            jconn  = sqlite3.connect(str(journal_path), check_same_thread=False)
            jconn.row_factory = sqlite3.Row
            init_db(jconn)

            # EOD sweep (once per day)
            if _is_eod() and not _eod_done_today(done_flags):
                logger.info("Running EOD sweep for {}", trade_date)
                eod_force_close(duck, jconn, trade_date)
                finalize_all_shadows(duck, jconn, trade_date)
                done_flags[trade_date] = True
                return

            for underlying in settings.UNDERLYINGS:
                # Expire stale pending recommendations
                expire_stale_recommendations(jconn, trade_date, snap_time)

                # Generate new recommendation if conditions met
                rec = generate_recommendation(duck, jconn, trade_date, snap_time, underlying)
                if rec:
                    logger.info(
                        "[{}] New recommendation: {} {} @ {}",
                        snap_time, underlying, rec.get("option_type"), rec.get("strike_price")
                    )

                # Track open positions
                track_open_positions(duck, jconn, trade_date, snap_time)

                # Shadow tracking
                track_shadow_positions(duck, jconn, trade_date, snap_time)

        except Exception as e:
            logger.error("Scheduler tick error: {}", e)
        finally:
            try:
                duck.close()
            except Exception:
                pass
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
        max_instances=1,
        coalesce=True,
    )
    return scheduler
