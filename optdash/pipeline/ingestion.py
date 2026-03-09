"""Parquet ingestion helpers — date/snap resolution utilities."""
import duckdb
from loguru import logger
from optdash.config import settings


def get_latest_snap(conn: duckdb.DuckDBPyConnection, trade_date: str, underlying: str) -> str | None:
    """Return the most recent snap_time for a given trade_date and underlying."""
    try:
        row = conn.execute("""
            SELECT MAX(snap_time) AS latest
            FROM options_data
            WHERE trade_date = ? AND underlying = ?
        """, [trade_date, underlying]).fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.warning("get_latest_snap failed: {}", e)
        return None


def get_available_dates(conn: duckdb.DuckDBPyConnection, underlying: str) -> list[str]:
    """Return all distinct trade_dates available for an underlying."""
    try:
        rows = conn.execute("""
            SELECT DISTINCT trade_date
            FROM options_data
            WHERE underlying = ?
            ORDER BY trade_date DESC
        """, [underlying]).fetchall()
        return [r[0] for r in rows]
    except Exception as e:
        logger.warning("get_available_dates failed: {}", e)
        return []


def get_snap_times(conn: duckdb.DuckDBPyConnection, trade_date: str, underlying: str) -> list[str]:
    """Return all snap_times for a trade_date sorted ascending."""
    try:
        rows = conn.execute("""
            SELECT DISTINCT snap_time
            FROM options_data
            WHERE trade_date = ? AND underlying = ?
            ORDER BY snap_time ASC
        """, [trade_date, underlying]).fetchall()
        return [r[0] for r in rows]
    except Exception as e:
        logger.warning("get_snap_times failed: {}", e)
        return []


def get_available_underlyings(conn: duckdb.DuckDBPyConnection, trade_date: str) -> list[str]:
    """Return all underlyings available for a given trade_date."""
    try:
        rows = conn.execute("""
            SELECT DISTINCT underlying
            FROM options_data
            WHERE trade_date = ?
            ORDER BY underlying
        """, [trade_date]).fetchall()
        return [r[0] for r in rows]
    except Exception as e:
        logger.warning("get_available_underlyings failed: {}", e)
        return []


def _safe_query(conn: duckdb.DuckDBPyConnection, sql: str, params: list = None) -> list[dict]:
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
        msg = str(e).lower()
        if "column" in msg and "does not exist" in msg:
            # Optional column absent in older Parquet files -- benign.
            logger.debug("Optional column missing -- returning empty: {}", e)
            return []
        # Missing view, missing table, or any other catalog error: propagate.
        raise
