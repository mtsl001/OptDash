"""
processor.py — Compute all derived columns before Parquet write.

Schema changes vs old pipeline (Fixes 1-7 applied in order):
  snap_time       : floor to 5-min grid BEFORE strftime (Fix 1)
  spot            : renamed from underlying_spot (Fix 2)
  bid_qty         : renamed from total_buy_qty (Fix 2)
  ask_qty         : renamed from total_sell_qty (Fix 2)
  instrument_type : FUTIDX/FUTSTK → FUT, OPTIDX → OPT (Fix 3)
  option_type     : set to 'FUT' for futures rows (Fix 4)
  fut_price       : effective_ltp for FUT rows, NaN for OPT (Fix 5)
  expiry_tier     : TIER1 (≤30 DTE) / TIER2 — NEAR/FAR collapsed (Fix 6)
  gex_k           : multiplied by lot_size (Fix 7)
  obi_raw         : uses renamed bid_qty / ask_qty
  coc             : uses renamed spot column
"""
import logging

import numpy as np
import pandas as pd

from config import (
    INDEX_UNDERLYINGS,
    STRIKE_INTERVALS,
    ATM_WINDOW_N,
    LOT_SIZES,
)
from atm import is_in_atm_window

logger = logging.getLogger(__name__)

_SQRT_2PI = np.sqrt(2.0 * np.pi)


def _bs_norm_pdf(x: np.ndarray) -> np.ndarray:
    """Standard-normal PDF: φ(x) = exp(-x²/2) / √(2π)."""
    return np.exp(-0.5 * x * x) / _SQRT_2PI


def _bs_d1(
    S: np.ndarray, K: np.ndarray, T: np.ndarray, r: float, sigma: np.ndarray
) -> np.ndarray:
    return (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))


def _bs_vanna(
    S: np.ndarray, K: np.ndarray, T: np.ndarray,
    sigma: np.ndarray, d1: np.ndarray, d2: np.ndarray
) -> np.ndarray:
    """
    Vanna = ∂Delta/∂sigma = -φ(d1) × d2 / sigma.
    The sigma in vex_k = OI × vanna × spot × sigma cancels the /sigma here.
    """
    return -_bs_norm_pdf(d1) * d2 / sigma


def _bs_charm(
    T: np.ndarray, r: float, sigma: np.ndarray,
    d1: np.ndarray, d2: np.ndarray
) -> np.ndarray:
    """
    Charm = -∂Delta/∂t (annual rate), divided by 365 → delta change per day.
    """
    sqrt_T = np.sqrt(T)
    charm_annual = -_bs_norm_pdf(d1) * (
        2.0 * r * T - d2 * sigma * sqrt_T
    ) / (2.0 * T * sigma * sqrt_T)
    return charm_annual / 365.0


def compute_derived_columns(
    df: pd.DataFrame,
    atm_windows: dict[str, dict],
) -> pd.DataFrame:
    """
    Add all derived columns to df.
    Fixes 1-7 applied in order: renames before computed columns that depend on them.
    """
    df = df.copy()

    # ── FIX 1: snap_time — floor to 5-min grid BEFORE strftime ───────────────
    rt = pd.to_datetime(df["record_time"])              # parse as-is (already IST)
    df["snap_time"]  = rt.dt.floor("5min").dt.strftime("%H:%M")  # 09:15:26 → "09:15"
    df["trade_date"] = rt.dt.strftime("%Y-%m-%d")

    # ── effective_ltp — compute BEFORE renames (uses ltp/close_price/close) ───
    if "effective_ltp" not in df.columns:
        df["effective_ltp"] = (
            df["ltp"]
            .combine_first(df["close_price"])
            .combine_first(df["close"])
        )
    else:
        needs_repair = df["effective_ltp"].isna()
        if needs_repair.any():
            df.loc[needs_repair, "effective_ltp"] = (
                df.loc[needs_repair, "ltp"]
                .combine_first(df.loc[needs_repair, "close_price"])
                .combine_first(df.loc[needs_repair, "close"])
            )

    # ── FIX 2: Rename columns to match new OptDash schema ────────────────────
    df.rename(columns={
        "underlying_spot": "spot",
        "total_buy_qty":   "bid_qty",
        "total_sell_qty":  "ask_qty",
    }, inplace=True)

    # ── FIX 3: instrument_type normalisation ──────────────────────────────────
    df["instrument_type"] = df["instrument_type"].replace({
        "FUTIDX": "FUT",
        "FUTSTK": "FUT",
        "OPTIDX": "OPT",
    })

    # ── FIX 4: option_type cleanup for futures rows ───────────────────────────
    df.loc[df["instrument_type"] == "FUT", "option_type"] = "FUT"

    # ── Back-fill spot for FUT rows from OPT rows (same underlying + snap) ───
    # Must run after Fix 2 (spot) and Fix 3 (OPT/FUT labels).
    opt_spot_map = (
        df[df["instrument_type"] == "OPT"]
        .dropna(subset=["spot"])
        .groupby(["underlying", "snap_time"])["spot"]
        .mean()
    )
    if not opt_spot_map.empty:
        fut_needs_spot = (
            (df["instrument_type"] == "FUT")
            & df["spot"].isna()
        )
        if fut_needs_spot.any():
            mapped_spot = df.loc[fut_needs_spot].set_index(
                ["underlying", "snap_time"]
            ).index.map(opt_spot_map.to_dict())
            df.loc[fut_needs_spot, "spot"] = mapped_spot
            logger.info(
                f"Back-filled spot for {fut_needs_spot.sum()} FUT rows."
            )

    # ── FIX 5: fut_price — map FUT effective_ltp to fut_price column ─────────
    df["fut_price"] = np.nan
    df.loc[df["instrument_type"] == "FUT", "fut_price"] = \
        df.loc[df["instrument_type"] == "FUT", "effective_ltp"]

    # ── Days to expiry ────────────────────────────────────────────────────────
    df["dte"] = (
        pd.to_datetime(df["expiry_date"]).dt.normalize()
        - rt.dt.normalize()
    ).dt.days.astype("Int64")

    # ── Options-only derivations (uses OPT after Fix 3) ──────────────────────
    opt_mask = df["instrument_type"] == "OPT"

    # Moneyness %: (strike - spot) / spot — uses "spot" after Fix 2
    df.loc[opt_mask, "moneyness_pct"] = (
        (df.loc[opt_mask, "strike_price"] - df.loc[opt_mask, "spot"])
        / df.loc[opt_mask, "spot"]
    ).round(6)

    # Liquidity turnover proxy
    df["L_proxy"] = (
        df["volume"].astype(float) * df["effective_ltp"]
    ).where(opt_mask)

    # ATM window flag
    df["in_atm_window"] = False
    for underlying, window in atm_windows.items():
        mask = (
            opt_mask
            & (df["underlying"] == underlying)
            & (df["strike_price"] >= window["lower_strike"])
            & (df["strike_price"] <= window["upper_strike"])
        )
        df.loc[mask, "in_atm_window"] = True

    # ── FIX 6: expiry_tier — TIER1 (≤30 DTE) / TIER2 ────────────────────────
    def _tier(dte_val):
        if pd.isna(dte_val): return "UNKNOWN"
        if dte_val <= 30:    return "TIER1"   # collapsed from TIER1_NEAR/FAR
        return "TIER2"

    df["expiry_tier"] = df["dte"].apply(_tier)

    # GEX directional multiplier: +1 CE, -1 PE
    df["d_dir"] = np.where(df["option_type"] == "CE",  1.0,
                  np.where(df["option_type"] == "PE", -1.0, np.nan))

    # ── FIX 7: GEX — add lot_size multiplier ─────────────────────────────────
    lot_sizes = LOT_SIZES   # {"NIFTY": 75, "BANKNIFTY": 15, ...}
    df["lot_size"] = df["underlying"].map(lot_sizes).fillna(1)
    gex_valid = (
        opt_mask
        & df["gamma"].notna()
        & df["oi"].notna()
        & df["spot"].notna()
        & df["d_dir"].notna()
    )
    df.loc[gex_valid, "gex_k"] = (
        df.loc[gex_valid, "gamma"].astype(float)
        * df.loc[gex_valid, "oi"].astype(float)
        * df.loc[gex_valid, "lot_size"].astype(float)     # ← added
        * df.loc[gex_valid, "spot"].astype(float) ** 2    # ← renamed spot
        * 0.01
        * df.loc[gex_valid, "d_dir"]
    )

    # ── VEX / CEX (uses "spot" after Fix 2) ──────────────────────────────────
    if opt_mask.any():
        df.loc[opt_mask, "vex_k"] = np.nan
        df.loc[opt_mask, "cex_k"] = np.nan

    vex_valid = (
        opt_mask
        & df["iv"].notna()
        & df["dte"].notna()
        & (df["dte"].fillna(0) >= 0)
        & df["spot"].notna()
        & (df["spot"].fillna(0) > 0)
        & df["strike_price"].notna()
        & (df["strike_price"].fillna(0) > 0)
        & df["oi"].notna()
        & (df["iv"].fillna(0) > 0)
        & df["d_dir"].notna()
    )

    if vex_valid.any():
        S     = df.loc[vex_valid, "spot"].astype(float).values
        K     = df.loc[vex_valid, "strike_price"].astype(float).values
        T     = np.maximum(df.loc[vex_valid, "dte"].astype(float).values, 1.0) / 365.0
        r     = 0.065
        sigma = df.loc[vex_valid, "iv"].astype(float).values / 100.0
        oi    = df.loc[vex_valid, "oi"].astype(float).values

        d1 = _bs_d1(S, K, T, r, sigma)
        d2 = d1 - sigma * np.sqrt(T)

        vanna       = _bs_vanna(S, K, T, sigma, d1, d2)
        charm_daily = _bs_charm(T, r, sigma, d1, d2)

        vex_k_raw = -oi * vanna * S * sigma / 1e6
        cex_k_raw =  oi * charm_daily / 1e6

        df.loc[vex_valid, "vex_k"] = np.round(vex_k_raw, 4)
        df.loc[vex_valid, "cex_k"] = np.round(cex_k_raw, 4)

        logger.debug(
            f"VEX/CEX computed: {vex_valid.sum():,} rows, "
            f"net VEX={vex_k_raw.sum():.2f}M, net CEX={cex_k_raw.sum():.2f}M"
        )

    # ── OBI — uses bid_qty / ask_qty (renamed in Fix 2) ──────────────────────
    tbq = df["bid_qty"].astype(float)
    tsq = df["ask_qty"].astype(float)
    total_qty = tbq + tsq
    df["obi_raw"] = np.where(total_qty > 0, (tbq - tsq) / total_qty, np.nan)

    # ── CoC for FUT rows — uses "spot" after Fix 2, "FUT" after Fix 3 ────────
    fut_mask = df["instrument_type"] == "FUT"
    df.loc[fut_mask, "coc"] = (
        df.loc[fut_mask, "effective_ltp"].astype(float)
        - df.loc[fut_mask, "spot"].astype(float)
    )

    # ── OI delta (change vs previous snapshot for same contract) ─────────────
    sort_cols  = ["underlying", "instrument_type", "expiry_date",
                  "strike_price", "option_type", "record_time"]
    group_cols = ["underlying", "instrument_type", "expiry_date",
                  "strike_price", "option_type"]

    df = df.sort_values(sort_cols)
    df["oi_delta"] = (
        df.groupby(group_cols)["oi"]
        .transform(lambda x: x.astype(float).diff())
        .fillna(0.0)
    )

    logger.info(
        f"Derived columns computed: {len(df):,} rows, "
        f"GEX non-null: {df['gex_k'].notna().sum():,}, "
        f"ATM window: {df['in_atm_window'].sum():,}"
    )
    return df
