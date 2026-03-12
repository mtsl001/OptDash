"""Parquet ingestion helpers — date/snap resolution utilities.

Error handling policy (P2-13)
------------------------------
All query helpers suppress ONLY ``duckdb.CatalogException`` where the
message fingerprint matches a missing *optional* column (i.e. the message
contains both "column" and "does not exist").  This is the benign case
where an older Parquet file predates a newly-added column -- the caller
receives an empty result and the gap is logged at DEBUG level.

Every other error -- missing ``options_data`` view, missing table, syntax
error, connection failure, disk error -- is re-raised so it propagates to
the API layer and surfaces in logs with a real traceback.  Callers must
not rely on empty returns as a proxy for "no data".

The same narrow suppression is used in ``_safe_query()`` (for analytics
queries that reference optional per-strike columns such as vex_k / cex_k).
"""
import duckdb
from loguru import logger
from optdash.config import settings


def _is_missing_optional_column(e: duckdb.CatalogException) -> bool:
    """True iff the CatalogException fingerprint matches a missing optional column.

    Fingerprint: message (lowercased) contains both "column" and "does not exist".
    This is the precise DuckDB error string for a SELECT referencing a column
    that is absent from the Parquet schema in older daily files.

    Intentionally does NOT match:
      - "Table ... does not exist"   (missing options_data view)
      - "View ... does not exist"    (startup refresh_views failure)
      - Any other catalog object     (missing schema, missing function, etc.)
    """
    msg = str(e).lower()
    return "column" in msg and "does not exist" in msg


def get_latest_snap(
    conn: duckdb.DuckDBPyConnection,
    trade_date: str,
    underlying: str,
) -> str | None:
    """Return the most recent snap_time for a given trade_date and underlying.

    Returns None only when no rows exist for the given date/underlying.
    Raises on all errors except a missing optional column.
    """
    try:
        row = conn.execute(
            """
            SELECT MAX(snap_time) AS latest
            FROM options_data
            WHERE trade_date = ? AND underlying = ?
            """,
            [trade_date, underlying],
        ).fetchone()
        return row[0] if row else None
    except duckdb.CatalogException as e:
        if _is_missing_optional_column(e):
            logger.debug("get_latest_snap: optional column missing -- {}", e)
            return None
        raise


def get_available_dates(
    conn: duckdb.DuckDBPyConnection,
    underlying: str,
) -> list[str]:
    """Return all distinct trade_dates available for an underlying, newest first.

    Returns [] only when no rows exist for the given underlying.
    Raises on all errors except a missing optional column.
    """
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT trade_date
            FROM options_data
            WHERE underlying = ?
            ORDER BY trade_date DESC
            """,
            [underlying],
        ).fetchall()
        return [r[0] for r in rows]
    except duckdb.CatalogException as e:
        if _is_missing_optional_column(e):
            logger.debug("get_available_dates: optional column missing -- {}", e)
            return []
        raise


def get_snap_times(
    conn: duckdb.DuckDBPyConnection,
    trade_date: str,
    underlying: str,
) -> list[str]:
    """Return all snap_times for a trade_date sorted ascending.

    Returns [] only when no rows exist for the given date/underlying.
    Raises on all errors except a missing optional column.
    """
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT snap_time
            FROM options_data
            WHERE trade_date = ? AND underlying = ?
            ORDER BY snap_time ASC
            """,
            [trade_date, underlying],
        ).fetchall()
        return [r[0] for r in rows]
    except duckdb.CatalogException as e:
        if _is_missing_optional_column(e):
            logger.debug("get_snap_times: optional column missing -- {}", e)
            return []
        raise


def get_available_underlyings(
    conn: duckdb.DuckDBPyConnection,
    trade_date: str,
) -> list[str]:
    """Return all underlyings available for a given trade_date.

    Returns [] only when no rows exist for the given date.
    Raises on all errors except a missing optional column.
    """
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT underlying
            FROM options_data
            WHERE trade_date = ?
            ORDER BY underlying
            """,
            [trade_date],
        ).fetchall()
        return [r[0] for r in rows]
    except duckdb.CatalogException as e:
        if _is_missing_optional_column(e):
            logger.debug("get_available_underlyings: optional column missing -- {}", e)
            return []
        raise


def _safe_query(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
    params: list = None,
) -> list[dict]:
    """
    Execute a DuckDB query safely, returning [] only when an *optional column*
    is absent from the Parquet schema (e.g. vex_k / cex_k in older files).

    Suppression scope (intentionally narrow)
    ----------------------------------------
    Only ``duckdb.CatalogException`` where the message contains both
    "column" and "does not exist" is suppressed.  This is the precise
    fingerprint of a missing optional column.

    Everything else -- including a missing ``options_data`` view, a missing
    table, or any non-catalog error -- is re-raised so callers receive a
    real error rather than silently empty results.  Previously the broad
    ``"catalog error"`` string match also swallowed missing-view errors,
    causing blank charts with no root-cause signal when startup
    refresh_views() had failed.
    """
    try:
        result = conn.execute(sql, params or [])
        cols   = [d[0] for d in result.description]
        return [dict(zip(cols, row)) for row in result.fetchall()]
    except duckdb.CatalogException as e:
        if _is_missing_optional_column(e):
            logger.debug("Optional column missing -- returning empty: {}", e)
            return []
        raise
