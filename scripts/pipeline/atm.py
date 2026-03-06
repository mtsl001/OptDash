"""
atm.py — Compute and persist the ATM strike window for each underlying.

ATM window logic:
  1. Take the day's first recorded underlying_spot (09:15 snapshot).
  2. Round to nearest strike interval -> ATM strike.
  3. ATM window = [ATM - N*interval, ATM + N*interval] where N = ATM_WINDOW_N (8).

The ATM window is computed once per day and saved to data/processed/atm_windows/.
"""
import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd

from config import STRIKE_INTERVALS, INDEX_UNDERLYINGS, ATM_WINDOW_N

logger = logging.getLogger(__name__)


def round_to_strike(price: float, interval: int) -> int:
    """Round price to the nearest valid strike for this underlying."""
    return int(round(price / interval) * interval)


def compute_atm_windows(df: pd.DataFrame, trade_date: date) -> dict[str, dict]:
    """
    Compute ATM window for each index underlying from the earliest snapshot.
    """
    windows: dict[str, dict] = {}

    for underlying in INDEX_UNDERLYINGS:
        subset = df[
            (df["underlying"] == underlying) &
            (df["instrument_type"] == "OPTIDX") &
            (df["underlying_spot"].notna())
        ].sort_values("record_time")

        if subset.empty:
            logger.warning(f"{trade_date}: No option data for {underlying} — skipping ATM window")
            continue

        open_spot = subset.iloc[0]["underlying_spot"]
        interval  = STRIKE_INTERVALS.get(underlying, 50)
        atm       = round_to_strike(open_spot, interval)
        n         = ATM_WINDOW_N

        windows[underlying] = {
            "atm_strike":   atm,
            "lower_strike": atm - n * interval,
            "upper_strike": atm + n * interval,
            "interval":     interval,
            "open_spot":    round(open_spot, 2),
            "n_strikes":    n,
        }
        logger.info(
            f"{trade_date} | {underlying}: open_spot={open_spot:.2f} "
            f"ATM={atm} window=[{atm - n*interval}, {atm + n*interval}]"
        )

    return windows


def save_atm_windows(windows: dict, trade_date: date, atm_dir: Path) -> None:
    """Persist ATM windows to data/processed/atm_windows/YYYY-MM-DD.json"""
    atm_dir.mkdir(parents=True, exist_ok=True)
    path = atm_dir / f"{trade_date}.json"
    with open(path, "w") as f:
        json.dump(windows, f, indent=2)
    logger.info(f"ATM windows saved: {path}")


def load_atm_windows(trade_date: date, atm_dir: Path) -> dict[str, dict]:
    """Load ATM windows for a given trade date. Returns {} if file missing."""
    path = atm_dir / f"{trade_date}.json"
    if not path.exists():
        logger.warning(f"ATM window file not found: {path}")
        return {}
    with open(path, "r") as f:
        return json.load(f)


def is_in_atm_window(
    underlying: str,
    strike_price: float,
    windows: dict[str, dict],
) -> bool:
    """Return True if strike_price is within the ATM window for this underlying."""
    w = windows.get(underlying)
    if w is None:
        return False
    return w["lower_strike"] <= strike_price <= w["upper_strike"]
