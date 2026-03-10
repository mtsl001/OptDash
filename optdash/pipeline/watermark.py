"""watermark.py — Atomic read/write of the BQ pull watermark.

Format  : 'YYYY-MM-DD HH:MM:SS' (naive — tz-info stripped; value is IST)
Storage : JSON {"last_record_time": "...", "saved_at": "..."}
          at settings.WATERMARK_PATH (default: data/watermark.json)
Atomicity: .tmp write then Path.replace() — POSIX atomic rename ensures
           DuckDB / BQ readers always see a complete JSON file, never a
           partial write left by a crash mid-save.

Sentinel (no file present)
--------------------------
Returns one second before midnight on BACKFILL_START_DATE − 1 day so the
very first pull_full_day call captures the complete BACKFILL_START_DATE day.
Example: BACKFILL_START_DATE='2026-02-17' → sentinel '2026-02-16 23:59:59'

to_str() helper
---------------
Converts a pandas Timestamp or Python datetime to the canonical watermark
string format. Used by processor.py after computing max(record_time).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from loguru import logger

from optdash.config import settings


def _initial_watermark() -> str:
    """One second before BACKFILL_START_DATE midnight — sentinel for first pull."""
    d = date.fromisoformat(settings.BACKFILL_START_DATE) - timedelta(days=1)
    return f"{d} 23:59:59"


def load(path: Path | None = None) -> str:
    """Return the persisted watermark string.

    Falls back to the initial sentinel when the watermark file does not yet
    exist (fresh install or first run after clearing data/).

    Parameters
    ----------
    path : override watermark file path (default: settings.WATERMARK_PATH)
    """
    p = path or settings.WATERMARK_PATH
    if not p.exists():
        wm = _initial_watermark()
        logger.info("No watermark file — using initial sentinel: {}", wm)
        return wm
    try:
        data = json.loads(p.read_text())
        wm   = data.get("last_record_time", _initial_watermark())
        logger.debug("Loaded watermark: {}", wm)
        return wm
    except (json.JSONDecodeError, KeyError) as exc:
        wm = _initial_watermark()
        logger.warning(
            "Watermark file corrupt ({}) — resetting to sentinel: {}", exc, wm
        )
        return wm


def save(ts_str: str, path: Path | None = None) -> None:
    """Atomically persist watermark to disk.

    Uses .tmp → Path.replace() (POSIX atomic rename) so a crash mid-write
    never produces a partial JSON file. The original file is untouched until
    the rename succeeds.

    Parameters
    ----------
    ts_str : watermark value in 'YYYY-MM-DD HH:MM:SS' format
    path   : override watermark file path (default: settings.WATERMARK_PATH)
    """
    p = path or settings.WATERMARK_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_record_time": ts_str,
        "saved_at":          datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(p)   # atomic on POSIX (same filesystem guaranteed)
    logger.debug("Watermark saved: {}", ts_str)


def to_str(ts) -> str:
    """Convert a pandas Timestamp or Python datetime to watermark string format.

    Strips tz-info if present (processor.py strips tz before computing max,
    but this guard ensures correctness if called with a tz-aware object).

    Parameters
    ----------
    ts : pandas.Timestamp or datetime.datetime
    """
    if hasattr(ts, "to_pydatetime"):
        ts = ts.to_pydatetime()
    # Strip tz-info without converting (value is already IST, label is spurious)
    if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
        ts = ts.replace(tzinfo=None)
    return ts.strftime("%Y-%m-%d %H:%M:%S")
