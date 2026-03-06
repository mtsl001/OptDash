"""
writer.py — Write processed DataFrames to Parquet (new OptDash layout).

Output layout:
    data/processed/
        trade_date=2026-03-05/
            NIFTY.parquet       ← OPT + FUT rows combined
            BANKNIFTY.parquet
            FINNIFTY.parquet
            ...

This layout is what duckdb_gateway.py globs:
    data/processed/trade_date=*/*.parquet

Key differences from old pipeline writer:
  - OPT and FUT rows land in the SAME file per underlying (not separate trees)
  - Target directory is data/processed/ (not data/raw/options/ + data/raw/futures/)
  - Column set reflects renamed columns from processor.py (spot, bid_qty, ask_qty)
  - Atomic temp-and-swap pattern retained from old writer

Row group size = 100,000:
  DuckDB allocates 1 thread per Parquet row group during scans.
  100k rows/group → 2-3 groups/day → DuckDB uses all available cores.
"""
import logging
import os
import time
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from config import (
    PARQUET_ROW_GROUP_SIZE,
    PARQUET_COMPRESSION,
    INDEX_UNDERLYINGS,
)

logger = logging.getLogger(__name__)

# Superset of OPT + FUT columns — absent columns are silently skipped
PROCESSED_WRITE_COLS = [
    "record_time", "snap_time", "trade_date",
    "underlying", "instrument_type", "instrument_key",
    "option_type", "expiry_date", "strike_price", "dte",
    "spot",                               # renamed from underlying_spot (Fix 2)
    "open", "high", "low", "close", "close_price", "ltp", "effective_ltp",
    "volume", "oi", "oi_delta",
    "bid_qty", "ask_qty",                 # renamed from total_buy/sell_qty (Fix 2)
    "iv", "delta", "theta", "gamma", "vega",
    "pcr",
    "fut_price",                          # FUT rows; NaN for OPT (Fix 5)
    "lot_size",                           # from Fix 7
    # Derived
    "moneyness_pct", "L_proxy", "in_atm_window", "expiry_tier",
    "d_dir", "gex_k", "obi_raw",
    "vex_k", "cex_k",
    "coc",                                # FUT rows; NaN for OPT
]


def _safe_write_parquet(table: pa.Table, path: Path) -> None:
    """
    Atomic temp-and-swap write with retry for Windows/OneDrive file locks.
    Writes to .tmp file first, then os.replace() to target path.
    """
    tmp_path = path.with_suffix(f".tmp_{int(time.time())}.parquet")
    path.parent.mkdir(parents=True, exist_ok=True)

    pq.write_table(
        table,
        str(tmp_path),
        compression=PARQUET_COMPRESSION,
        row_group_size=PARQUET_ROW_GROUP_SIZE,
        use_dictionary=True,
        write_statistics=True,
    )

    max_retries = 10
    for attempt in range(max_retries):
        try:
            if path.exists():
                os.replace(str(tmp_path), str(path))
            else:
                tmp_path.rename(path)
            return
        except PermissionError as exc:
            if attempt < max_retries - 1:
                wait = 0.5 * (1.5 ** attempt)
                logger.warning(
                    f"File locked: {path.name}. "
                    f"Retry {attempt + 1}/{max_retries} in {wait:.1f}s…"
                )
                time.sleep(wait)
            else:
                logger.error(
                    f"Failed to write {path.name} after {max_retries} attempts: {exc}"
                )
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass
                raise
        except Exception as exc:
            logger.error(f"Unexpected error writing {path.name}: {exc}")
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            raise

    if tmp_path.exists():
        try:
            tmp_path.unlink()
        except Exception:
            pass


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    """Write DataFrame to path; silently skips absent columns."""
    available = [c for c in PROCESSED_WRITE_COLS if c in df.columns]
    missing   = [c for c in PROCESSED_WRITE_COLS if c not in df.columns]
    if missing:
        logger.debug(f"Columns absent (skipped): {missing}")
    table = pa.Table.from_pandas(df[available], preserve_index=False)
    _safe_write_parquet(table, path)
    size_kb = path.stat().st_size // 1024
    logger.debug(f"Written {len(df):,} rows → {path.name} ({size_kb} KB)")


def write_day_parquet(
    df: pd.DataFrame,
    trade_date: date,
    processed_dir: Path,
) -> dict[str, str]:
    """
    Write one full trading day to Parquet (called by backfill.py).

    One file per underlying combining ALL instrument_type rows (OPT + FUT).
    Directory: processed_dir/trade_date=YYYY-MM-DD/UNDERLYING.parquet

    Returns dict {underlying: filepath_str} for logging.
    """
    ds      = str(trade_date)
    day_dir = processed_dir / f"trade_date={ds}"
    written: dict[str, str] = {}

    for underlying in sorted(df["underlying"].unique()):
        sub = df[df["underlying"] == underlying]
        if sub.empty:
            continue
        path = day_dir / f"{underlying}.parquet"
        _write_parquet(sub, path)
        written[underlying] = str(path)
        logger.info(
            f"[{ds}] {underlying}: "
            f"OPT={int((sub['instrument_type']=='OPT').sum())}, "
            f"FUT={int((sub['instrument_type']=='FUT').sum())} rows "
            f"→ {path.name}"
        )

    logger.info(f"[{ds}] Parquet write complete — {len(written)} file(s)")
    return written


def write_incremental_parquet(
    df: pd.DataFrame,
    processed_dir: Path,
) -> None:
    """
    Write incremental pull by merge-overwrite per underlying (called by
    run_pipeline.py and gap_fill.py).

    Strategy: read existing file → concat new rows → dedup on
    (snap_time, instrument_key) keeping last → atomic write.

    File count stays O(trading_days) not O(snapshots) — DuckDB picks up
    the overwritten file on the next query without view refresh.
    """
    if df.empty:
        return

    for ds in df["trade_date"].unique():
        day_df  = df[df["trade_date"] == ds].copy()
        day_dir = processed_dir / f"trade_date={ds}"

        for underlying in sorted(day_df["underlying"].unique()):
            new_rows  = day_df[day_df["underlying"] == underlying]
            available = [c for c in PROCESSED_WRITE_COLS if c in new_rows.columns]
            new_rows  = new_rows[available]

            path = day_dir / f"{underlying}.parquet"

            if path.exists():
                existing = None
                for attempt in range(5):
                    try:
                        existing = pq.read_table(str(path)).to_pandas()
                        break
                    except (PermissionError, IOError) as exc:
                        if attempt < 4:
                            wait = 0.5 * (1.5 ** attempt)
                            logger.warning(
                                f"[{ds}] Read locked: {path.name}. "
                                f"Retry {attempt + 1}/5 in {wait:.1f}s…"
                            )
                            time.sleep(wait)
                        else:
                            logger.error(
                                f"[{ds}] Cannot read {path.name} after 5 attempts "
                                f"— skipping to prevent data loss: {exc}"
                            )

                if existing is None:
                    # Safety: never overwrite if we cannot read existing data
                    continue

                try:
                    existing = existing[
                        [c for c in available if c in existing.columns]
                    ]
                    merged = pd.concat([existing, new_rows], ignore_index=True)
                except Exception as exc:
                    logger.warning(
                        f"[{ds}] Merge failed for {underlying}: {exc} — overwriting"
                    )
                    merged = new_rows
            else:
                merged = new_rows

            # Dedup: keep LAST for each (snap_time, instrument_key) combo
            if "snap_time" in merged.columns and "instrument_key" in merged.columns:
                merged = (
                    merged
                    .sort_values("snap_time")
                    .drop_duplicates(
                        subset=["snap_time", "instrument_key"], keep="last"
                    )
                    .reset_index(drop=True)
                )

            path.parent.mkdir(parents=True, exist_ok=True)
            table = pa.Table.from_pandas(merged, preserve_index=False)
            _safe_write_parquet(table, path)
            logger.debug(
                f"[{ds}] {underlying}: merged {len(new_rows):,} new → "
                f"{len(merged):,} total rows"
            )
