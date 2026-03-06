"""Parquet writer -- 1 file per underlying per calendar day.

Each 5-minute scheduler tick calls write_snap() for every underlying.
Because Parquet does not support native append, the writer uses a
read-merge-rewrite pattern:

  1. If a file already exists for today's underlying, load it.
  2. Drop any existing rows for the incoming snap_time (idempotent re-runs).
  3. Concat the new rows, sort by snap_time, write back atomically.

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

    try:
        if path.exists():
            existing  = pd.read_parquet(path)
            snap_time = snap_df["snap_time"].iloc[0]
            # Drop previous rows for this snap_time so retries are idempotent.
            existing  = existing[existing["snap_time"] != snap_time]
            merged    = pd.concat([existing, snap_df], ignore_index=True)
        else:
            merged = snap_df.copy()

        merged = merged.sort_values(
            ["snap_time", "strike_price", "option_type"],
            ignore_index=True,
        )

        table = pa.Table.from_pandas(merged, preserve_index=False)
        pq.write_table(
            table, path,
            compression="snappy",
            write_statistics=True,   # enables min/max pushdown in DuckDB
        )
        logger.debug(
            "[Writer] {}/{} -- {} rows -> {}",
            trade_date, underlying, len(merged), path.name,
        )

    except Exception as e:
        logger.error(
            "[Writer] Failed to write {}/{}: {}", trade_date, underlying, e
        )
