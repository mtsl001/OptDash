"""watermark.py — Atomic read/write of the BQ pull watermark.

Format: "YYYY-MM-DD HH:MM:SS" (naive, tz-info stripped — value is IST).
Storage: JSON {"last_record_time": "...", "saved_at": "..."}
Atomicity: .tmp write then Path.replace() — POSIX atomic rename.

Initial sentinel: one second before midnight on (BACKFILL_START_DATE − 1)
so the very first pull_full_day() captures the full first backfill day.

P2-12: saved_at uses IST-aware datetime so the audit timestamp in the
JSON reflects the correct IST wall-clock time regardless of the server
OS timezone (UTC on most VPS / cloud deployments).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

from optdash.config import settings

IST = ZoneInfo("Asia/Kolkata")


def _initial_watermark() -> str:
    """Sentinel: one second before BACKFILL_START_DATE so first pull captures that full day."""
    d = date.fromisoformat(settings.BACKFILL_START_DATE) - timedelta(days=1)
    return f"{d} 23:59:59"


def load(path: Path | None = None) -> str:
    """Return watermark string, or initial sentinel if watermark file does not exist."""
    p = path or settings.WATERMARK_PATH
    if not p.exists():
        wm = _initial_watermark()
        logger.info("No watermark file found — using initial sentinel: {}", wm)
        return wm
    data = json.loads(p.read_text())
    wm   = data.get("last_record_time", _initial_watermark())
    logger.debug("Loaded watermark: {}", wm)
    return wm


def save(ts_str: str, path: Path | None = None) -> None:
    """Atomically persist watermark string. ts_str format: 'YYYY-MM-DD HH:MM:SS'.

    P2-12: saved_at uses IST-aware datetime.now(IST).replace(tzinfo=None)
    so the audit field shows IST wall-clock time in the JSON regardless of
    the server OS timezone.  .replace(tzinfo=None) keeps the stored format
    as 'YYYY-MM-DD HH:MM:SS' with no +05:30 suffix.
    """
    p = path or settings.WATERMARK_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_record_time": ts_str,
        # P2-12: IST wall-clock time, tz-info stripped for clean JSON format.
        "saved_at": datetime.now(IST).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
    }
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(p)   # atomic POSIX rename — DuckDB / reader always sees complete file
    logger.debug("Watermark saved: {}", ts_str)


def to_str(ts) -> str:
    """Convert pandas Timestamp or datetime to watermark string."""
    if hasattr(ts, "to_pydatetime"):
        ts = ts.to_pydatetime()
    return ts.strftime("%Y-%m-%d %H:%M:%S")
