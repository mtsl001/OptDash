"""
config.py — Pipeline configuration for scripts/pipeline/.

Imports shared constants from optdash.config (single source of truth).
All pipeline-specific settings live here.

See Phase 5E spec.
"""
import os
from pathlib import Path

# ── Import from new OptDash package ──────────────────────────────────────────
from optdash.config import settings   # single source of truth for LOT_SIZES, etc.

# ── Directory roots ───────────────────────────────────────────────────────────
DATA_ROOT       = settings.DATA_ROOT
PROCESSED_DIR   = DATA_ROOT / "processed"          # trade_date=YYYY-MM-DD/UNDERLYING.parquet
ATM_WINDOWS_DIR = DATA_ROOT / "processed" / "atm_windows"
WATERMARK_PATH  = DATA_ROOT / "watermark.json"

# Back-compat alias — verbatim-ported files (gap_fill, backfill) reference RAW_DIR.
# Pointing it to PROCESSED_DIR means all their path logic redirects correctly.
RAW_DIR = PROCESSED_DIR

# ── BigQuery ──────────────────────────────────────────────────────────────────
BQ_TABLE_FQN     = os.environ["BQ_TABLE_FQN"]      # e.g. "project.dataset.options_snaps"
BQ_PROJECT       = os.environ.get("BQ_PROJECT", BQ_TABLE_FQN.split(".")[0])
CREDENTIALS_PATH = Path(
    os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
)

BQ_SELECT_COLS = [
    "record_time",
    "underlying",
    "instrument_type",
    "instrument_key",
    "option_type",
    "expiry_date",
    "strike_price",
    "underlying_spot",
    "open", "high", "low", "close",
    "ltp", "close_price",
    "volume", "oi",
    "total_buy_qty", "total_sell_qty",
    "iv", "delta", "theta", "gamma", "vega",
    "pcr",
]

# ── Underlyings & Strike grid ─────────────────────────────────────────────────
LOT_SIZES         = settings.LOT_SIZES                # {"NIFTY": 75, "BANKNIFTY": 15, ...}
INDEX_UNDERLYINGS = settings.UNDERLYINGS              # ["NIFTY", "BANKNIFTY", ...]
STRIKE_INTERVALS  = settings.STRIKE_INTERVALS         # {"NIFTY": 50, ...}

ATM_WINDOW_N = 8    # ±N strikes around ATM

# ── Expiry tier thresholds (Fix 6) ────────────────────────────────────────────
# New OptDash collapses TIER1_NEAR/FAR into a single TIER1 (≤30 DTE).
TIER1_MAX_DTE      = 30
# Back-compat aliases so verbatim-ported files that import these names still work.
TIER1_NEAR_MAX_DTE = TIER1_MAX_DTE
TIER1_FAR_MIN_DTE  = TIER1_MAX_DTE + 1

# ── Parquet write settings ────────────────────────────────────────────────────
PARQUET_ROW_GROUP_SIZE = 100_000   # 1 DuckDB thread per row group
PARQUET_COMPRESSION    = "snappy"

# ── Backfill window ───────────────────────────────────────────────────────────
BACKFILL_START_DATE = os.environ.get("BACKFILL_START_DATE", "2026-01-01")
BACKFILL_END_DATE   = os.environ.get("BACKFILL_END_DATE",   "2026-03-05")
