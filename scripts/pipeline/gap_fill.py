"""
gap_fill.py — Automatic intraday gap detection and recovery.

Problem solved
--------------
If the pipeline stops mid-day (crash, power cut, disconnect), the watermark
is left at the last successfully written record_time — e.g. 12:35.  The
APScheduler only fires during live market hours (09:16–15:30), so if you
restart after market close or the next morning the missed 12:35–15:30 data
would be permanently absent from Parquet.

Solution
--------
run_gap_fill() is called at pipeline startup (before the live scheduler):
  1. Reads the current watermark.
  2. For each past trading day where the watermark is BEFORE 15:30, pulls
     the missing window from BigQuery.
  3. Processes and writes using the same merge-overwrite strategy as the
     live pipeline.
  4. Updates the watermark after each successful recovery.

New OptDash path layout (adapted from old pipeline)
---------------------------------------------------
Old: data/raw/options/trade_date=YYYY-MM-DD/UNDERLYING.parquet
New: data/processed/trade_date=YYYY-MM-DD/UNDERLYING.parquet  (OPT + FUT combined)

This is idempotent — safe to run every time.
"""
import logging
from datetime import date, datetime, time, timedelta

import pandas as pd
from google.cloud import bigquery

from config import (
    PROCESSED_DIR,
    ATM_WINDOWS_DIR,
    WATERMARK_PATH,
    BQ_TABLE_FQN,
    BQ_SELECT_COLS,
)
from bq_client import get_bq_client
from validator import validate_dataframe
from atm import compute_atm_windows, save_atm_windows, load_atm_windows
from processor import compute_derived_columns
from writer import write_incremental_parquet
from duckdb_setup import safe_refresh_views
import watermark as wm
from market_calendar import is_trading_day

logger = logging.getLogger(__name__)

# Market close time — anything at or before this is “complete” for the day
MARKET_CLOSE_TIME = time(15, 30, 0)

# Minimum gap (minutes) that warrants a recovery pull.
MIN_GAP_MINUTES = 4


def _watermark_date_and_time(wm_str: str) -> tuple[date, time]:
    """Parse 'YYYY-MM-DD HH:MM:SS' into (date, time)."""
    dt = datetime.strptime(wm_str, "%Y-%m-%d %H:%M:%S")
    return dt.date(), dt.time()


def _day_has_data(check_date: date) -> bool:
    """
    Return True if any Parquet file exists for check_date in the new layout:
        data/processed/trade_date=YYYY-MM-DD/*.parquet
    """
    day_dir = PROCESSED_DIR / f"trade_date={check_date}"
    return day_dir.exists() and any(day_dir.glob("*.parquet"))


def _needs_gap_fill(wm_str: str) -> list[tuple[date, str]]:
    """
    Examine the watermark and return a list of (trade_date, from_wm) pairs
    that need a gap-fill pull.  Handles all restart scenarios:

    Case 1a — Same-day, mid-session restart:
              Watermark = today 11:10, now = 14:20, market still open.
    Case 1b — Same-day, after-hours restart:
              Watermark = today 11:10, now = 16:00.
    Case 2  — Watermark from a prior trading day:
              Pull the rest of that day up to market close.
    Case 3  — Entire trading day(s) skipped between wm_date+1 and today-1.
    Case 4  — Today is a trading day but we have no data yet.

    Returns: [(trade_date, watermark_str), ...] ordered chronologically.
    """
    now      = datetime.now()
    today    = now.date()
    now_time = now.time()
    in_market = time(9, 15, 0) <= now_time <= MARKET_CLOSE_TIME
    after_mkt = now_time > MARKET_CLOSE_TIME

    wm_date, wm_time_val = _watermark_date_and_time(wm_str)

    gaps: list[tuple[date, str]] = []

    def _check_and_add(
        check_date: date,
        from_wm: str,
        cutoff: time | None = None,
    ) -> None:
        """Add check_date to gaps if the watermark lags the cutoff by >MIN_GAP_MINUTES."""
        if not is_trading_day(check_date):
            return
        effective_cutoff = cutoff if cutoff is not None else MARKET_CLOSE_TIME
        dt               = datetime.strptime(from_wm, "%Y-%m-%d %H:%M:%S")
        cutoff_dt        = datetime.combine(check_date, effective_cutoff)
        gap_minutes      = (cutoff_dt - dt).total_seconds() / 60
        if gap_minutes > MIN_GAP_MINUTES:
            logger.info(
                f"[gap_fill] Gap on {check_date}: watermark=\"{from_wm}\", "
                f"cutoff={effective_cutoff.strftime('%H:%M')}, "
                f"missing ~{gap_minutes:.0f} min"
            )
            gaps.append((check_date, from_wm))

    if wm_date == today:
        # Case 1a: same-day, market still open
        if in_market:
            _check_and_add(today, wm_str, cutoff=now_time)
        # Case 1b: same-day, after hours
        elif after_mkt:
            _check_and_add(today, wm_str, cutoff=MARKET_CLOSE_TIME)

    elif wm_date < today:
        # Case 2: prior trading day — pull remainder of that day
        _check_and_add(wm_date, wm_str, cutoff=MARKET_CLOSE_TIME)

        # Case 3: entirely skipped trading days
        current = wm_date + timedelta(days=1)
        while current < today:
            if is_trading_day(current) and not _day_has_data(current):
                from_wm = (
                    datetime.combine(current, time(0, 0, 0))
                    - timedelta(seconds=1)
                ).strftime("%Y-%m-%d %H:%M:%S")
                logger.info(
                    f"[gap_fill] Entire trading day {current} has no data — "
                    f"will pull full session"
                )
                gaps.append((current, from_wm))
            current += timedelta(days=1)

        # Case 4: today is a trading day and market is open / just closed
        if is_trading_day(today) and (in_market or after_mkt):
            if not _day_has_data(today):
                today_wm = (
                    datetime.combine(today, time(0, 0, 0)) - timedelta(seconds=1)
                ).strftime("%Y-%m-%d %H:%M:%S")
                cutoff = now_time if in_market else MARKET_CLOSE_TIME
                _check_and_add(today, today_wm, cutoff=cutoff)

    return gaps


def _pull_day_gap(
    client: bigquery.Client,
    trade_date: date,
    from_wm: str,
) -> pd.DataFrame:
    """
    Pull all rows for trade_date where record_time > from_wm.
    Bounded to a single day's partition — does not cross date boundaries.
    """
    cols       = ", ".join(BQ_SELECT_COLS)
    ds         = trade_date.strftime("%Y-%m-%d")
    wm_literal = f'TIMESTAMP("{from_wm}")'

    query = f"""
        SELECT {cols}
        FROM `{BQ_TABLE_FQN}`
        WHERE DATE(record_time) = '{ds}'
          AND record_time > {wm_literal}
        ORDER BY record_time ASC
    """

    logger.info(f"[gap_fill] Pulling {ds}: record_time > {from_wm}")
    df = client.query(query).to_dataframe()

    if df.empty:
        logger.warning(f"[gap_fill] {ds}: 0 rows returned after {from_wm}")
        return df

    df["record_time"] = pd.to_datetime(df["record_time"]).dt.tz_localize(None)
    logger.info(
        f"[gap_fill] {ds}: {len(df):,} rows pulled, "
        f"range {df['record_time'].min()} → {df['record_time'].max()}"
    )
    return df


def _process_and_write(df: pd.DataFrame, trade_date: date) -> str:
    """
    Run the standard pipeline stages on recovered data and return new watermark.
    Writes to data/processed/trade_date=YYYY-MM-DD/UNDERLYING.parquet.
    """
    df = validate_dataframe(df)

    # Load or compute ATM windows for this day
    atm_windows = load_atm_windows(trade_date, ATM_WINDOWS_DIR)
    if not atm_windows:
        logger.info(f"[gap_fill] Computing ATM windows for {trade_date}")
        atm_windows = compute_atm_windows(df, trade_date)
        if atm_windows:
            save_atm_windows(atm_windows, trade_date, ATM_WINDOWS_DIR)
        else:
            logger.warning(
                f"[gap_fill] Could not compute ATM windows for {trade_date} "
                f"— using full strike universe"
            )

    # Derived columns (all 7 processor fixes applied)
    df = compute_derived_columns(df, atm_windows)

    # Write — merge-overwrite into data/processed/trade_date=DS/UNDERLYING.parquet
    write_incremental_parquet(df, PROCESSED_DIR)

    # No-op in new OptDash — API process manages DuckDB views
    safe_refresh_views()

    max_ts  = df["record_time"].max()
    new_wm  = wm.from_timestamp(max_ts)
    n_snaps = df["snap_time"].nunique()
    snaps   = sorted(df["snap_time"].unique())
    logger.info(
        f"[gap_fill] {trade_date} recovery complete: {len(df):,} rows, "
        f"{n_snaps} snapshots [{', '.join(snaps)}] → watermark {new_wm}"
    )
    return new_wm


def run_gap_fill() -> None:
    """
    Detect and fill all intraday gaps in Parquet data.
    Called from run_pipeline.py before the live scheduler starts.
    Safe to call every time — exits immediately if no gaps are found.
    """
    current_wm = wm.load(WATERMARK_PATH)
    logger.info(f"[gap_fill] Checking for gaps. Watermark: {current_wm}")

    gaps = _needs_gap_fill(current_wm)

    if not gaps:
        logger.info("[gap_fill] No gaps detected — pipeline data is complete")
        return

    logger.info(
        f"[gap_fill] {len(gaps)} gap(s) to recover: "
        + ", ".join(str(d) for d, _ in gaps)
    )

    try:
        client = get_bq_client()
    except Exception as exc:
        logger.error(f"[gap_fill] Could not create BigQuery client: {exc}")
        return

    for trade_date, from_wm in gaps:
        logger.info(f"[gap_fill] ── Recovering {trade_date} ──")
        try:
            df = _pull_day_gap(client, trade_date, from_wm)
            if df.empty:
                logger.warning(
                    f"[gap_fill] {trade_date}: No rows after {from_wm}. "
                    f"Possibly an early-close day or data not yet in BQ."
                )
                if trade_date < date.today():
                    eod_wm = datetime.combine(
                        trade_date, MARKET_CLOSE_TIME
                    ).strftime("%Y-%m-%d %H:%M:%S")
                    wm.save(WATERMARK_PATH, eod_wm)
                    logger.info(
                        f"[gap_fill] Advanced watermark to EOD for past day: {eod_wm}"
                    )
                else:
                    logger.info(
                        "[gap_fill] Today's gap recovery returned 0 rows — "
                        "will retry in next pull"
                    )
                continue

            new_wm = _process_and_write(df, trade_date)
            wm.save(WATERMARK_PATH, new_wm)

        except Exception as exc:
            logger.error(
                f"[gap_fill] Recovery failed for {trade_date}: {exc}",
                exc_info=True,
            )
            # Do NOT advance watermark — next startup will retry this day


def gap_fill_status() -> dict:
    """
    Returns a dict describing current gap status. Useful for startup logging.

    Returns:
        {
          "watermark": "2026-03-05 12:35:00",
          "gaps_found": 1,
          "gaps": [{"date": "2026-03-05", "from": "2026-03-05 12:35:00",
                    "gap_minutes": 175}]
        }
    """
    current_wm = wm.load(WATERMARK_PATH)
    gaps       = _needs_gap_fill(current_wm)

    result = {
        "watermark":  current_wm,
        "gaps_found": len(gaps),
        "gaps": [],
    }
    for trade_date, from_wm in gaps:
        from_dt      = datetime.strptime(from_wm, "%Y-%m-%d %H:%M:%S")
        market_close = datetime.combine(trade_date, MARKET_CLOSE_TIME)
        gap_min      = max(0, (market_close - from_dt).total_seconds() / 60)
        result["gaps"].append({
            "date":        str(trade_date),
            "from":        from_wm,
            "gap_minutes": round(gap_min),
        })
    return result
