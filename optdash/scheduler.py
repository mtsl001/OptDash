"""APScheduler -- drives recommendation generation and position tracking.

Tick structure (every SCHEDULER_INTERVAL_SECONDS, market hours only):
  1. Expire stale pending recommendations  -> once per tick (all underlyings)
  2. Pre-compute gate cache for open trades -> once per tick (all open positions)
  3. Generate recommendations              -> once per underlying
  4. Track open positions                  -> once per tick (uses gate cache)
  5. Track shadow positions                -> once per tick (all shadow trades)
  6. EOD sweep                             -> once per calendar day

DuckDB connection
-----------------
The scheduler reuses the shared in-process DuckDB gateway connection via
duckdb_gateway.get_conn().  The old pattern (duckdb.connect(duck_path,
read_only=True) per tick) opened a file-based connection that had no
options_data Parquet view registered, causing every analytics call to fail
silently with 'Table options_data not found'.

SQLite connection
-----------------
The scheduler reuses the shared SQLite connection passed in via
journal_conn (owned by deps.startup / app.state.journal).  The old
pattern (_make_jconn per tick) opened a new connection every 5 minutes,
creating ~72 open/close cycles per trading day and bypassing the shared
WAL-mode connection already maintained by the API layer.

Both API request handlers and the scheduler run in the same asyncio event
loop (AsyncIOScheduler).  Because all DuckDB calls are synchronous and
never yield to the event loop, each tick runs atomically -- no concurrent
DuckDB access is possible.
"""
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
from optdash.ai.journal.trades import get_open_trades
from optdash.analytics.environment import get_environment_score
from optdash.pipeline.purge import purge_old_raw_parquets
from optdash.pipeline.duckdb_gateway import get_conn, refresh_views

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


def _build_gate_cache(
    duck, trade_date: str, snap_time: str, jconn: sqlite3.Connection
) -> dict:
    """Pre-compute gate scores for all currently open positions.

    Fix-L (F-04): track_open_positions() was calling get_environment_score()
    (7 DuckDB aggregations) once per open trade. This pre-computation runs
    the gate exactly once per unique underlying and passes the result in so
    track_open_positions() can skip the per-position re-query.

    Gate is computed with the actual option_type (direction) so C9 (2 pts)
    scores correctly and NO_GO verdicts are not under-counted.
    """
    open_trades = get_open_trades(jconn)
    cache: dict[str, dict] = {}
    for t in open_trades:
        underlying = t["underlying"]
        if underlying in cache:
            continue  # only compute once per underlying
        try:
            cache[underlying] = get_environment_score(
                duck, trade_date, snap_time,
                underlying, direction=t["option_type"]
            )
        except Exception as e:
            logger.warning("gate_cache pre-compute failed for {}: {}", underlying, e)
            cache[underlying] = {
                "score": 0, "verdict": "NO_GO",
                "conditions": {}, "session": ""
            }
    return cache


def create_scheduler(
    journal_conn: sqlite3.Connection,
) -> AsyncIOScheduler:
    """
    Returns a configured AsyncIOScheduler.
    Call scheduler.start() inside FastAPI lifespan (after deps.startup()).

    journal_conn: shared SQLite connection owned by deps.startup()
    (stored on app.state.journal).  The scheduler reuses this connection
    directly -- no new SQLite connections are opened per tick.

    Note: duck_path removed -- the scheduler now uses the shared DuckDB
    gateway connection (get_conn()) so it reads the same registered
    Parquet view as the API.  duckdb_gateway.startup() must be called
    first -- deps.startup() does this automatically.
    """
    done_flags: dict[str, bool] = {}

    async def tick() -> None:
        if not _is_market_hours():
            return

        trade_date = _today_str()
        snap_time  = _snap_time_str()

        try:
            # Shared in-process DuckDB -- gateway owns its lifecycle.
            duck  = get_conn()
            # Shared SQLite -- lifecycle owned by deps.shutdown().
            # Do NOT close jconn here.
            jconn = journal_conn

            # -- EOD sweep (once per calendar day) ----------------------------
            if _is_eod() and not _eod_done_today(done_flags):
                logger.info("Running EOD sweep for {}", trade_date)
                eod_force_close(duck, jconn, trade_date)
                finalize_all_shadows(duck, jconn, trade_date)

                # Purge stale raw Parquets (once per day at EOD).
                try:
                    purge_old_raw_parquets(
                        Path(settings.DATA_ROOT),
                        settings.RAW_PARQUET_RETENTION_DAYS,
                    )
                except Exception as purge_err:
                    logger.error("raw Parquet purge failed: {}", purge_err)

                # Roll the DuckDB view forward so tomorrow's new partition
                # directory is visible on the first tick after midnight.
                try:
                    refresh_views(duck)
                    logger.info("DuckDB view refreshed for next trading day.")
                except Exception as rv_err:
                    logger.error("refresh_views failed: {}", rv_err)

                # Trim done_flags to last 7 entries to prevent unbounded growth.
                if len(done_flags) > 7:
                    oldest = sorted(done_flags)[0]
                    del done_flags[oldest]

                done_flags[trade_date] = True
                return  # skip normal tick on EOD

            # -- Step 1: Expire stale pending recommendations ------------------
            expire_stale_recommendations(jconn, trade_date, snap_time)

            # -- Step 2: Pre-compute gate cache for all open positions ---------
            # get_environment_score runs 7 DuckDB aggregations per underlying.
            # Caching here avoids repeating those queries once per open trade
            # inside track_open_positions() (Fix-L / F-04).
            gate_cache = _build_gate_cache(duck, trade_date, snap_time, jconn)

            # -- Step 3: Generate new recommendations (per underlying) ---------
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

            # -- Step 4: Track all open positions (gate_cache avoids N+1) ------
            track_open_positions(duck, jconn, trade_date, snap_time,
                                 gate_cache=gate_cache)

            # -- Step 5: Track all shadow positions ----------------------------
            track_shadow_positions(duck, jconn, trade_date, snap_time)

        except Exception as e:
            logger.error("Scheduler tick error @ {}: {}", snap_time, e)

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
