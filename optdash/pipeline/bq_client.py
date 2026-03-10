"""bq_client.py — BigQuery auth + pull functions with retry.

Singleton pattern: _bq_client created once on first call, reused forever.
Retry: tenacity exponential backoff — 4 attempts, 4→60s wait.

table_fqn parameter controls which BQ table is queried:
  settings.BQ_FQN_ARCHIVE  (upxtx_ar) — full history → backfill
  settings.BQ_FQN_LIVE     (upxtx)    — rolling live  → gap fill + incremental

record_time in BQ is UTC-labeled but numerically IST.
processor.py strips tz-info without converting (tz_localize(None)).

Column selection is driven by settings.BQ_SELECT_COLS.
Excluded deliberately:
  close_price     — yesterday's settlement; wrong as ltp fallback
  last_trade_time — not needed
  rho             — not provided by Upstox API
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

_bq_client = None   # module-level singleton — created once, reused forever


def get_bq_client():
    """Return (or create) the singleton BQ client."""
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

    Used by backfill.py against settings.BQ_FQN_ARCHIVE (upxtx_ar).

    Parameters
    ----------
    trade_date_str : ISO date string e.g. '2026-03-07'
    table_fqn      : fully-qualified BQ table name e.g.
                     'universal-ion-437606-b7.bgquery.upxtx_ar'
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
    """Pull rows with record_time strictly after watermark.

    Used by incremental.py against settings.BQ_FQN_LIVE (upxtx).

    Parameters
    ----------
    watermark  : 'YYYY-MM-DD HH:MM:SS' naive string (tz-stripped IST value)
    table_fqn  : fully-qualified BQ table name
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
def pull_day_gap(
    trade_date_str: str,
    from_watermark: str,
    table_fqn: str,
) -> pd.DataFrame:
    """Pull rows for one calendar day strictly after from_watermark.

    Used by gap_fill.py against settings.BQ_FQN_LIVE (upxtx).
    After the 06:35 IST sync upxtx is empty — returns 0 rows (not an error).

    Parameters
    ----------
    trade_date_str : ISO date string e.g. '2026-03-10'
    from_watermark : 'YYYY-MM-DD HH:MM:SS' — rows <= this timestamp are skipped
    table_fqn      : fully-qualified BQ table name
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
