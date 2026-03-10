"""bq_client.py — BigQuery auth + pull functions with retry.

Singleton pattern: _bq_client created once on first call, reused forever.
Retry: tenacity exponential backoff — 4 attempts, 4→60s wait.

table_fqn parameter controls which BQ table is queried:
  settings.BQ_FQN_ARCHIVE  (upxtx_ar) — full history → backfill
  settings.BQ_FQN_LIVE     (upxtx)    — rolling live  → gap fill + incremental

record_time in BQ is UTC-labelled but numerically IST.
Processor strips tz-info without conversion — see processor._strip_tz().
"""
from __future__ import annotations

import pandas as pd
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from optdash.config import settings

_bq_client = None   # module-level singleton; initialised on first get_bq_client() call


def get_bq_client():
    """Return singleton BQ client, creating it on first call."""
    global _bq_client
    if _bq_client is None:
        from google.oauth2 import service_account
        from google.cloud import bigquery

        creds = service_account.Credentials.from_service_account_file(
            str(settings.BQ_CREDENTIALS_PATH),
            scopes=["https://www.googleapis.com/auth/bigquery.readonly"],
        )
        _bq_client = bigquery.Client(
            project=settings.BQ_PROJECT,
            credentials=creds,
        )
        logger.info("BQ client initialised (project={})", settings.BQ_PROJECT)
    return _bq_client


def _cols() -> str:
    """Comma-separated SELECT column list from settings.BQ_SELECT_COLS."""
    return ", ".join(settings.BQ_SELECT_COLS)


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    reraise=True,
)
def pull_full_day(trade_date_str: str, table_fqn: str) -> pd.DataFrame:
    """Pull all rows for one calendar day from table_fqn.

    Used by backfill (upxtx_ar). Returns empty DataFrame if no rows.
    """
    query = (
        f"SELECT {_cols()} FROM `{table_fqn}` "
        f"WHERE DATE(record_time) = '{trade_date_str}' "
        f"ORDER BY record_time"
    )
    logger.debug("BQ pull_full_day: {} from {}", trade_date_str, table_fqn)
    df = get_bq_client().query(query).to_dataframe()
    logger.info("pull_full_day {}: {} rows", trade_date_str, len(df))
    return df


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    reraise=True,
)
def pull_incremental(watermark: str, table_fqn: str) -> pd.DataFrame:
    """Pull rows with record_time > watermark from table_fqn.

    Used by live incremental tick (upxtx). Returns empty DataFrame if
    no new rows since last watermark (normal between feed cadence windows).
    """
    query = (
        f"SELECT {_cols()} FROM `{table_fqn}` "
        f"WHERE record_time > TIMESTAMP('{watermark}') "
        f"ORDER BY record_time"
    )
    logger.debug("BQ pull_incremental: watermark={}", watermark)
    df = get_bq_client().query(query).to_dataframe()
    if not df.empty:
        logger.info("pull_incremental: {} new rows since {}", len(df), watermark)
    return df


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    reraise=True,
)
def pull_day_gap(trade_date_str: str, from_watermark: str, table_fqn: str) -> pd.DataFrame:
    """Pull rows for one calendar day strictly after from_watermark.

    Used by gap fill (upxtx). Handles the 06:35 IST sync scenario:
    if upxtx is empty (post-sync, pre-market), returns empty DataFrame
    which gap_fill.py logs as INFO (not WARNING).
    """
    query = (
        f"SELECT {_cols()} FROM `{table_fqn}` "
        f"WHERE DATE(record_time) = '{trade_date_str}' "
        f"  AND record_time > TIMESTAMP('{from_watermark}') "
        f"ORDER BY record_time"
    )
    logger.debug("BQ pull_day_gap: {} after {}", trade_date_str, from_watermark)
    df = get_bq_client().query(query).to_dataframe()
    logger.info("pull_day_gap {}: {} rows", trade_date_str, len(df))
    return df
