"""Parquet writer -- 1 file per underlying per calendar day.

Each 5-minute scheduler tick calls write_snap() for every underlying.
Because Parquet does not support native append, the writer uses a
read-merge-rewrite pattern:

  1. If a file already exists for today's underlying, load it.
  2. Drop any existing rows for ALL incoming snap_times (idempotent re-runs).
  3. Concat the new rows, sort by snap_time, write back atomically.

Atomicity
---------
Writes go to a ``.tmp`` sibling first, then Path.replace() performs an
atomic rename (POSIX syscall) so DuckDB always sees either the previous
complete file or the new complete file -- never a partial write.

Concurrency
-----------
filelock.FileLock serialises concurrent read-merge-write access on a
``.lock`` sibling.  This guards against scheduler-restart overlaps where
two writer instances could otherwise race on the same file.

File layout
-----------
  data/processed/trade_date=YYYY-MM-DD/
      NIFTY.parquet
      BANKNIFTY.parquet
      FINNIFTY.parquet
      ...

The ``trade_date=`` directory prefix is the hive partition key DuckDB
uses for partition pruning -- trade_date is auto-extracted from the
path and does not need to be stored as a column.  The ``underlying``
column is stored inside each file so analytics queries can filter by
both dimensions.

Performance
-----------
At 75 snaps x ~500 strikes per underlying, a full daily file is
~37 500 rows.  pyarrow write of that size is typically <50 ms --
well within the 5-minute scheduler tick budget.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from filelock import FileLock
from loguru import logger

PROCESSED_SUBDIR   = "processed"
_PARTITION_PREFIX  = "trade_date="


def parquet_path(data_root: Path, trade_date: str, underlying: str) -> Path:
    """Return the canonical Parquet path for a given day + underlying."""
    return (
        data_root
        / PROCESSED_SUBDIR
        / f"{_PARTITION_PREFIX}{trade_date}"
        / f"{underlying}.parquet"
    )


def write_snap(
    data_root:  Path,
    trade_date: str,
    underlying: str,
    snap_df:    pd.DataFrame,
) -> None:
    """Merge *snap_df* rows into today's Parquet file for *underlying*.

    Parameters
    ----------
    data_root:  root data directory (settings.DATA_ROOT)
    trade_date: ISO date string, e.g. '2026-03-06'
    underlying: instrument name, e.g. 'NIFTY'
    snap_df:    DataFrame for this snap -- must include a 'snap_time' column
    """
    if snap_df.empty:
        return

    path = parquet_path(data_root, trade_date, underlying)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Advisory lock: serialises concurrent read-merge-write on the same file.
    # Timeout=30 s: if another writer holds the lock for >30 s something is
    # badly wrong; let the exception propagate rather than stacking up writers.
    lock_path = path.with_suffix(".lock")
    with FileLock(str(lock_path), timeout=30):
        _write_snap_locked(path, snap_df, trade_date, underlying)


def _write_snap_locked(
    path:       Path,
    snap_df:    pd.DataFrame,
    trade_date: str,
    underlying: str,
) -> None:
    """Core read-merge-write logic, called with the file lock already held."""
    tmp_path = path.with_suffix(".tmp")
    try:
        if path.exists():
            existing = pd.read_parquet(path)
            # F3 fix: drop ALL incoming snap_times, not just iloc[0].
            # A backfill/retry batch may cover multiple snap windows; dropping
            # only the first snap_time left duplicates for the rest.
            incoming_snaps = set(snap_df["snap_time"].unique())
            existing = existing[
                ~existing["snap_time"].isin(incoming_snaps)
            ]
            merged = pd.concat([existing, snap_df], ignore_index=True)
        else:
            merged = snap_df.copy()

        merged = merged.sort_values(
            ["snap_time", "strike_price", "option_type"],
            ignore_index=True,
        )

        table = pa.Table.from_pandas(merged, preserve_index=False)

        # F2 fix: write to .tmp then atomically rename.
        # Path.replace() is an atomic rename syscall on POSIX; DuckDB always
        # sees either the previous complete file or this new complete file.
        pq.write_table(
            table, tmp_path,
            compression="snappy",
            write_statistics=True,   # enables min/max pushdown in DuckDB
        )
        tmp_path.replace(path)   # atomic on POSIX (same filesystem)

        logger.debug(
            "[Writer] {}/{} -- {} rows -> {}",
            trade_date, underlying, len(merged), path.name,
        )

    except Exception:
        # Never leave a partial .tmp behind; the original file is untouched.
        tmp_path.unlink(missing_ok=True)
        raise
