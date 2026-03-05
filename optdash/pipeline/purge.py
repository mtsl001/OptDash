"""Utility — purge stale raw Parquet files from the data directory.

Called once per day at EOD_SWEEP_TIME by both the pipeline scheduler
(optdash/pipeline/scheduler.py) and the active AI scheduler
(optdash/scheduler.py).  Keeping the logic here avoids duplicating
code and prevents cross-imports between the two scheduler modules.
"""
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

IST = ZoneInfo("Asia/Kolkata")


def purge_old_raw_parquets(data_root: Path, retention_days: int) -> None:
    """Delete raw Parquet files older than *retention_days* calendar days.

    Scans ``data/raw/options/`` and ``data/raw/futures/`` for files whose
    stem matches an ISO date (``YYYY-MM-DD``).  Files with any other naming
    pattern are skipped silently so non-data files are never deleted.

    Processed Parquets (``data/processed/``) are **never** touched; they
    serve as the permanent audit trail for DuckDB analytics.
    """
    cutoff   = datetime.now(IST).date() - timedelta(days=retention_days)
    raw_dirs = [data_root / "raw" / "options", data_root / "raw" / "futures"]
    for raw_dir in raw_dirs:
        if not raw_dir.exists():
            continue
        for parquet_file in raw_dir.glob("*.parquet"):
            try:
                file_date = date.fromisoformat(parquet_file.stem)  # YYYY-MM-DD
                if file_date < cutoff:
                    parquet_file.unlink()
                    logger.info("[Purge] Deleted stale raw Parquet: {}", parquet_file)
            except ValueError:
                pass  # Non-date filename — skip silently
