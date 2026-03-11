"""processor.py — Transform raw BQ DataFrame into PARQUET_SCHEMA-compliant rows.

BQ → Parquet column mapping (full):
  record_time         → snap_time (floor 5min HH:MM), trade_date (YYYY-MM-DD)
  underlying          → underlying (direct)
  instrument_type     → instrument_type (OPTIDX→OPT, FUTIDX→FUT)
  option_type         → option_type (direct; NULL for FUT rows)
  expiry_date         → expiry_date (M/D/YYYY → YYYY-MM-DD ISO)
  strike_price        → strike_price (float64)
  underlying_spot     → spot (float64)
  ltp + close         → ltp (COALESCE: ltp first, then intraday close fallback)
  volume              → volume (int64)
  oi                  → oi (int64)
  total_buy_qty       → bid_qty (int64)
  total_sell_qty      → ask_qty (int64)
  iv/delta/theta/gamma/vega → direct (float64)
  (computed)          → fut_price (near-FUT ltp back-filled per snap_time)
  (computed)          → dte ((expiry_date − trade_date).days)
  (computed)          → expiry_tier (TIER1≤15, TIER2 16–45, TIER3>45)
  (computed)          → gex (γ × OI × lot × spot² × 0.01 × dir; CE=+1, PE=−1)
  (computed)          → vex (OI × lot × vanna × spot / 1e6)
  (computed)          → cex (OI × lot × charm / 1e6)

Columns NOT written to Parquet:
  instrument_key  — used only to identify FUT rows internally
  close_price     — yesterday's settlement; wrong as ltp fallback
  last_trade_time — not needed by any analytics module
  rho             — not provided by Upstox / BQ feed

Entry point: process_and_write(df, duck_conn=None) → new watermark str | None
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

from optdash.config import settings
from optdash.pipeline.writer import write_snap, parquet_path
from optdash.pipeline.watermark import to_str as wm_str

# GEX sign convention: CE=+1 (dealers net long gamma — pinning effect)
#                      PE=−1 (dealers net short gamma — directional pressure)
# Matches gex.py _classify_regime(): SUM(gex) > 0 → POSITIVE_CHOP.
_GEX_SIGN  = {"CE": +1, "PE": -1}
_VEX_SCALE = 1e6   # Rs M — matches vex_cex.py / 1e6 divisor
_CEX_SCALE = 1e6

# Output column order — MUST match PARQUET_SCHEMA field order in writer.py exactly.
_OUT_COLS = [
    "snap_time", "underlying", "strike_price", "expiry_date",
    "option_type", "instrument_type", "ltp", "iv", "delta", "theta",
    "gamma", "vega", "spot", "fut_price", "oi", "volume",
    "bid_qty", "ask_qty", "gex", "vex", "cex", "expiry_tier", "dte",
]


def process_and_write(df: pd.DataFrame, duck_conn=None) -> str | None:
    """Main entry point: transform BQ DataFrame and write Parquet files.

    Parameters
    ----------
    df:         Raw BQ DataFrame from bq_client pull functions.
    duck_conn:  Optional live DuckDB connection. When provided and a new
                partition directory is created, refresh_views() is called
                so the new day is immediately queryable without restart.

    Returns
    -------
    New watermark string ('YYYY-MM-DD HH:MM:SS') or None if df is empty.
    """
    if df is None or df.empty:
        return None

    df = _strip_tz(df)
    df = _normalize_types(df)
    df = _compute_snap_and_dates(df)

    new_wm = wm_str(df["_rt"].max())

    for underlying, u_df in df.groupby("underlying"):
        lot_size = settings.LOT_SIZES.get(str(underlying))
        if lot_size is None:
            logger.warning("No LOT_SIZES entry for {} — skipping", underlying)
            continue
        try:
            _process_underlying(str(underlying), u_df.copy(), lot_size, duck_conn)
        except Exception as e:
            logger.error("processor: failed for {}: {}", underlying, e)
            raise

    return new_wm


# ── Internal pipeline steps ──────────────────────────────────────────────

def _strip_tz(df: pd.DataFrame) -> pd.DataFrame:
    """Strip tz-info from record_time without any timezone conversion.

    BQ returns record_time as a tz-aware Series (UTC-labelled) but the
    numeric wall-clock values are IST — no actual UTC→IST conversion was
    applied at ingest time. We strip tz-info only, preserving the IST
    wall-clock numbers unchanged.

    tz_localize(None) raises TypeError on an already-tz-aware Series;
    tz_convert(None) is the correct call to detach tz-info from tz-aware
    timestamps. Naive timestamps (no tz) pass through unchanged.
    """
    df = df.copy()
    rt = pd.to_datetime(df["record_time"])
    df["_rt"] = rt.dt.tz_convert(None) if rt.dt.tz is not None else rt
    return df


def _normalize_types(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise instrument_type, expiry_date format, column renames and casts."""
    df = df.copy()

    # instrument_type: OPTIDX→OPT, FUTIDX→FUT.
    # Must be done BEFORE _compute_fut_price() which filters WHERE instrument_type='FUT'.
    _itype_map = {"OPTIDX": "OPT", "FUTIDX": "FUT"}
    df["instrument_type"] = (
        df["instrument_type"].map(_itype_map).fillna(df["instrument_type"])
    )

    # expiry_date: M/D/YYYY → YYYY-MM-DD ISO.
    # Must be done BEFORE dte calculation so pd.to_datetime() parses correctly.
    # Also required for correct string sort in DuckDB IV term-structure queries.
    df["expiry_date"] = (
        pd.to_datetime(df["expiry_date"], dayfirst=False).dt.strftime("%Y-%m-%d")
    )

    # effective_ltp: primary price ltp, fallback to intraday running close.
    # close_price (yesterday's settlement) is excluded from BQ_SELECT_COLS entirely.
    df["ltp"]   = pd.to_numeric(df["ltp"],   errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["ltp"]   = df["ltp"].combine_first(df["close"])

    # spot from underlying_spot
    df["spot"] = pd.to_numeric(df["underlying_spot"], errors="coerce")

    # bid_qty / ask_qty from cumulative day buy/sell totals
    df["bid_qty"] = pd.to_numeric(df["total_buy_qty"],  errors="coerce").astype("Int64")
    df["ask_qty"] = pd.to_numeric(df["total_sell_qty"], errors="coerce").astype("Int64")

    # Greek + price casts to float64
    for col in ["strike_price", "iv", "delta", "theta", "gamma", "vega"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # OI / volume casts
    df["oi"]     = pd.to_numeric(df["oi"],     errors="coerce").astype("Int64")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64")

    return df


def _compute_snap_and_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Compute snap_time, trade_date, dte, expiry_tier from normalised columns."""
    df = df.copy()

    df["snap_time"]  = df["_rt"].dt.floor("5min").dt.strftime("%H:%M")
    df["trade_date"] = df["_rt"].dt.date.astype(str)

    # dte: calendar days from trade_date to expiry_date
    df["_td"] = pd.to_datetime(df["trade_date"])
    df["_ed"] = pd.to_datetime(df["expiry_date"])
    df["dte"] = (df["_ed"] - df["_td"]).dt.days.astype("Int32")

    df["expiry_tier"] = df["dte"].apply(_assign_tier)
    return df


def _assign_tier(dte_val) -> str | None:
    """TIER1=0–15, TIER2=16–45, TIER3>45.

    None for FUT rows (dte may be negative or NA for non-expiring instruments).
    Tier boundary at 45 (not 30) captures next-monthly expiry (~30–45 DTE)
    correctly in near GEX (gex.py IN TIER1, TIER2). A 30-day cutoff pushes
    mid-month options into TIER3, understating near GEX on days 16–30.
    """
    if pd.isna(dte_val) or int(dte_val) < 0:
        return None
    v = int(dte_val)
    if v <= 15:
        return "TIER1"
    if v <= 45:
        return "TIER2"
    return "TIER3"


def _process_underlying(
    underlying: str,
    df: pd.DataFrame,
    lot_size: int,
    duck_conn,
) -> None:
    """Compute FUT price, GEX/VEX/CEX, then write per-trade_date Parquets."""
    df = _compute_fut_price(df, underlying)
    df = _compute_gex_vex_cex(df, lot_size)

    for trade_date, td_df in df.groupby("trade_date"):
        _write_trade_date(str(underlying), str(trade_date), td_df, duck_conn)


def _compute_fut_price(df: pd.DataFrame, underlying: str) -> pd.DataFrame:
    """Back-fill near-month futures ltp onto all rows per snap_time.

    Near-month = minimum non-negative dte among FUT rows for that snap.
    Merged left so OPT rows without a matching snap get NaN fut_price.
    """
    df = df.copy()
    df["fut_price"] = np.nan

    # instrument_type must already be normalised to 'FUT' (done in _normalize_types)
    fut = df[
        (df["instrument_type"] == "FUT")
        & df["dte"].notna()
        & (df["dte"] >= 0)
    ].copy()

    if fut.empty:
        logger.warning("No FUT rows found for {} — fut_price will be NULL", underlying)
        return df

    # Near-month: minimum dte per snap_time
    near = (
        fut.sort_values("dte")
        .groupby("snap_time", as_index=False)
        .first()[["snap_time", "ltp"]]
        .rename(columns={"ltp": "_fut_ltp"})
    )
    df = df.merge(near, on="snap_time", how="left")
    df["fut_price"] = df["_fut_ltp"]
    return df.drop(columns=["_fut_ltp"])


def _compute_gex_vex_cex(df: pd.DataFrame, lot_size: int) -> pd.DataFrame:
    """Compute per-strike GEX, VEX, CEX for OPT rows. FUT rows remain NaN.

    GEX formula:
      γ × OI × lot_size × spot² × 0.01 × dir
      dir: CE=+1 (dealers long gamma, pinning), PE=−1 (dealers short gamma)

    VEX (Vanna Exposure) — approximate BSM:
      vanna ≈ δ × (1 − |δ|) / (spot × σ × √T)
      vex   = OI × lot × vanna × spot / 1e6

    CEX (Charm Exposure) — approximate BSM:
      charm ≈ −θ / (spot × σ × √T)
      cex   = OI × lot × charm / 1e6

    iv is percentage (e.g. 21.33) — divide by 100 to get decimal σ.
    dte=0 (expiry day): sqrt_t=NaN → vex/cex=NaN (GEX still valid).

    P0-3: vanna is clipped to [−VANNA_CLIP, +VANNA_CLIP] before the VEX
    multiplication.  Near-zero IV rows from the NSE feed produce a
    near-zero denominator, yielding vanna of 100–10,000+ that permanently
    corrupts VEX totals in Parquet.  See config.py VANNA_CLIP for
    calibration details.

    P0-2: charm is clipped to [−CHARM_CLIP, +CHARM_CLIP] before the CEX
    multiplication.  Same failure mode as vanna: near-zero IV rows produce
    charm of ±10,000–100,000 that permanently corrupts CEX SUM() totals in
    Parquet and all downstream vex_cex.py analytics.  See config.py
    CHARM_CLIP for calibration details.
    """
    df = df.copy()
    df["gex"] = np.nan
    df["vex"] = np.nan
    df["cex"] = np.nan

    mask = (
        (df["instrument_type"] == "OPT")
        & df["option_type"].isin(["CE", "PE"])
    )
    opts = df[mask].copy()
    if opts.empty:
        return df

    opts["_dir"] = opts["option_type"].map(_GEX_SIGN).fillna(0)

    # GEX
    spot_sq      = opts["spot"] ** 2
    opts["gex"]  = (
        opts["gamma"] * opts["oi"] * lot_size * spot_sq * 0.01 * opts["_dir"]
    )

    # Shared denominator for VEX / CEX
    sigma  = opts["iv"] / 100.0                          # percentage → decimal
    sqrt_t = (opts["dte"].astype(float) / 365.0).apply(
        lambda x: math.sqrt(x) if x > 0 else np.nan     # dte=0 → NaN (safe)
    )
    denom  = (opts["spot"] * sigma * sqrt_t).replace(0, np.nan)

    # VEX
    # P0-3: clip vanna before multiplying into VEX.  The replace(0, np.nan)
    # above only catches exact-zero denominators; near-zero IV rows produce
    # a small-but-nonzero denom and vanna of 100–10,000+.  Clipping to
    # [-VANNA_CLIP, +VANNA_CLIP] (default ±50) absorbs all corrupt rows
    # without ever affecting real option data (normal range: 0.0005–0.005).
    vanna        = opts["delta"] * (1.0 - opts["delta"].abs()) / denom
    vanna        = vanna.clip(-settings.VANNA_CLIP, settings.VANNA_CLIP)  # P0-3
    opts["vex"]  = (opts["oi"] * lot_size * vanna * opts["spot"]) / _VEX_SCALE

    # CEX
    # P0-2: clip charm before multiplying into CEX.  Near-zero IV rows produce
    # charm = -theta / denom where denom ≈ 0, yielding ±10,000–100,000.
    # These corrupt CEX SUM() totals permanently in Parquet.  Clipping to
    # [-CHARM_CLIP, +CHARM_CLIP] (default ±50) is symmetric with VANNA_CLIP
    # and safe: normal ATM charm is 0.001–0.01 (~5,000× below the clip).
    charm        = -opts["theta"] / denom
    charm        = charm.clip(-settings.CHARM_CLIP, settings.CHARM_CLIP)  # P0-2
    opts["cex"]  = (opts["oi"] * lot_size * charm) / _CEX_SCALE

    df.loc[mask, ["gex", "vex", "cex"]] = opts[["gex", "vex", "cex"]].values
    return df


def _write_trade_date(
    underlying:  str,
    trade_date:  str,
    td_df:       pd.DataFrame,
    duck_conn,
) -> None:
    """Write all snaps for one (underlying, trade_date) pair.

    Each snap is written individually via write_snap() which handles
    read-merge-rewrite atomically under FileLock.
    refresh_views() is called only when a new partition directory is
    created (first write for a new trade_date).
    """
    data_root     = Path(settings.DATA_ROOT)
    path          = parquet_path(data_root, trade_date, underlying)
    new_partition = not path.parent.exists()

    # Select output columns in PARQUET_SCHEMA order; fill any absent with NaN
    out_df = td_df.reindex(columns=_OUT_COLS)

    for snap_time, snap_df in out_df.groupby("snap_time"):
        write_snap(data_root, trade_date, underlying, snap_df.reset_index(drop=True))

    n_snaps = out_df["snap_time"].nunique()
    logger.debug(
        "processor: {}/{} — {} snaps ({} rows)",
        trade_date, underlying, n_snaps, len(out_df),
    )

    if new_partition and duck_conn is not None:
        from optdash.pipeline.duckdb_gateway import refresh_views
        try:
            refresh_views(duck_conn)
            logger.info("DuckDB view refreshed (new partition: {})", trade_date)
        except Exception as e:
            logger.error("refresh_views after new partition failed: {}", e)
