"""
validator.py — Schema and data quality validation for BigQuery pulls.

validate_dataframe() is called on every DataFrame before processing.
It logs all issues and raises ValueError if any CRITICAL issue is found.
Non-critical issues are logged as warnings and corrected in-place.
"""
import logging

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

CRITICAL_NOT_NULL = [
    "record_time",
    "underlying",
    "instrument_type",
    "expiry_date",
]

EXPECTED_FLOAT_COLS = [
    "underlying_spot", "open", "high", "low", "close", "ltp",
    "iv", "delta", "theta", "gamma", "vega", "strike_price",
    "close_price",
]
EXPECTED_INT_COLS  = ["volume", "oi", "total_buy_qty", "total_sell_qty"]
EXPECTED_STR_COLS  = ["underlying", "instrument_type", "option_type", "instrument_key"]


def validate_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Validate and lightly clean the raw DataFrame from BigQuery.

    Raises:
        ValueError: If any CRITICAL column is missing or entirely null.

    Returns:
        Cleaned DataFrame with dtype casts, effective_ltp, and expiry_date normalised.
    """
    issues: list[str] = []

    for col in CRITICAL_NOT_NULL:
        if col not in df.columns:
            raise ValueError(f"CRITICAL: Column '{col}' missing from DataFrame")
        if df[col].isna().all():
            raise ValueError(f"CRITICAL: Column '{col}' is entirely null")

    for col in EXPECTED_FLOAT_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in EXPECTED_INT_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

    for col in EXPECTED_STR_COLS:
        if col in df.columns and df[col].dtype == object:
            df[col] = df[col].str.strip()

    df["effective_ltp"] = df["ltp"].combine_first(
        df.get("close_price", pd.Series(dtype=float))
    ).combine_first(
        df.get("close", pd.Series(dtype=float))
    )

    null_ltp = df["effective_ltp"].isna().sum()
    if null_ltp > 0:
        issues.append(f"effective_ltp null in {null_ltp} rows after COALESCE")

    opt_mask  = df["instrument_type"] == "OPTIDX"
    null_spot = df.loc[opt_mask, "underlying_spot"].isna().sum()
    if null_spot > 0:
        issues.append(f"underlying_spot null in {null_spot} OPTIDX rows")

    if "iv" in df.columns:
        bad_iv = df.loc[opt_mask & df["iv"].notna() & (df["iv"] < 0), "iv"].count()
        if bad_iv > 0:
            df.loc[opt_mask & (df["iv"] < 0), "iv"] = np.nan
            issues.append(f"Negative IV corrected to NaN in {bad_iv} rows")

    df["expiry_date"] = pd.to_datetime(df["expiry_date"])

    n_opt = opt_mask.sum()
    n_fut = (~opt_mask).sum()
    logger.info(
        f"Validated {len(df):,} rows — OPTIDX: {n_opt:,}, FUTIDX/FUTSTK: {n_fut:,}"
    )
    if issues:
        for issue in issues:
            logger.warning(f"Validation: {issue}")

    return df
