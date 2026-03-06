"""
watermark.py — Manage the incremental pull watermark.

Watermark format: "YYYY-MM-DD HH:MM:SS"
This matches BigQuery record_time values (which are IST despite the UTC label).

Atomicity: Uses write-to-temp + rename to prevent corruption if the process
is killed mid-write. On macOS/APFS, Path.replace() is atomic within a volume.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# Pull everything from this point if no watermark file exists.
# Set to one second before backfill start so the first pull gets 2026-01-01.
INITIAL_WATERMARK = "2025-12-31 23:59:59"


def load(path: Path) -> str:
    """
    Load the last successfully pulled record_time string.
    Returns INITIAL_WATERMARK if the file does not exist.
    """
    if not path.exists():
        logger.info(
            f"No watermark file at {path} — using initial watermark {INITIAL_WATERMARK}"
        )
        return INITIAL_WATERMARK
    with open(path, "r") as f:
        data = json.load(f)
    wm = data.get("last_record_time", INITIAL_WATERMARK)
    logger.debug(f"Loaded watermark: {wm}")
    return wm


def save(path: Path, record_time_str: str) -> None:
    """
    Atomically save the new watermark.
    record_time_str: "YYYY-MM-DD HH:MM:SS" (IST, no timezone label)
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_record_time": record_time_str,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    tmp.replace(path)     # Atomic on APFS/HFS+
    logger.debug(f"Watermark saved: {record_time_str}")


def from_timestamp(ts) -> str:
    """Convert a pandas Timestamp or datetime to watermark string format."""
    if hasattr(ts, "to_pydatetime"):
        ts = ts.to_pydatetime()
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def to_bq_literal(wm: str) -> str:
    """
    Format watermark for use inside a BigQuery SQL WHERE clause.
    Returns: TIMESTAMP("2026-03-05 15:30:00")
    """
    return f'TIMESTAMP("{wm}")'
