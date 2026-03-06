"""
bq_client.py — BigQuery client: authentication and data pull functions.

All functions return a pandas DataFrame with the exact schema defined in config.py.
The record_time column is returned as a pandas Timestamp (naive, representing IST).
"""
import logging
from datetime import date
from typing import Optional

import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from config import (
    BQ_TABLE_FQN,
    BQ_SELECT_COLS,
    CREDENTIALS_PATH,
    BQ_PROJECT,
)

logger = logging.getLogger(__name__)

_RETRYABLE = (Exception,)


def get_bq_client() -> bigquery.Client:
    """
    Create and return an authenticated BigQuery client.
    Uses service account JSON key specified in config.CREDENTIALS_PATH.
    The GOOGLE_APPLICATION_CREDENTIALS env var also works (set in .env),
    but we pass the path explicitly for reliability.
    """
    credentials = service_account.Credentials.from_service_account_file(
        str(CREDENTIALS_PATH),
        scopes=["https://www.googleapis.com/auth/bigquery"],
    )
    client = bigquery.Client(project=BQ_PROJECT, credentials=credentials)
    logger.info(f"BigQuery client created for project {BQ_PROJECT}")
    return client


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    retry=retry_if_exception_type(_RETRYABLE),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def pull_incremental(client: bigquery.Client, watermark: str) -> pd.DataFrame:
    """
    Pull all rows with record_time strictly greater than watermark.
    watermark: "YYYY-MM-DD HH:MM:SS" format (IST values).

    Returns an empty DataFrame if no new rows exist.
    """
    cols = ", ".join(BQ_SELECT_COLS)
    wm_literal = f'TIMESTAMP("{watermark}")'

    query = f"""
        SELECT {cols}
        FROM `{BQ_TABLE_FQN}`
        WHERE record_time > {wm_literal}
        ORDER BY record_time ASC
    """

    logger.debug(f"Incremental pull: record_time > {watermark}")
    df = client.query(query).to_dataframe()

    if df.empty:
        logger.debug("Incremental pull: 0 rows (expected between snapshots)")
        return df

    df["record_time"] = pd.to_datetime(df["record_time"]).dt.tz_localize(None)
    logger.info(f"Incremental pull: {len(df):,} rows, "
                f"time range {df['record_time'].min()} \u2192 {df['record_time'].max()}")
    return df


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    retry=retry_if_exception_type(_RETRYABLE),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def pull_full_day(client: bigquery.Client, trade_date_str: str) -> pd.DataFrame:
    """
    Pull ALL rows for a specific trade date. Used by backfill.
    trade_date_str: "YYYY-MM-DD"
    """
    cols = ", ".join(BQ_SELECT_COLS)

    query = f"""
        SELECT {cols}
        FROM `{BQ_TABLE_FQN}`
        WHERE DATE(record_time) = '{trade_date_str}'
        ORDER BY record_time ASC, underlying ASC, instrument_type ASC,
                 expiry_date ASC, strike_price ASC, option_type ASC
    """

    logger.info(f"Full-day pull: {trade_date_str}")
    df = client.query(query).to_dataframe()

    if df.empty:
        logger.warning(f"Full-day pull {trade_date_str}: 0 rows returned")
        return df

    df["record_time"] = pd.to_datetime(df["record_time"]).dt.tz_localize(None)
    logger.info(f"Full-day pull {trade_date_str}: {len(df):,} rows, "
                f"{df['underlying'].nunique()} underlyings")
    return df
