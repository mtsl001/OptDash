"""DuckDB connection — read-only view over Parquet files."""
import duckdb
from pathlib import Path
from loguru import logger
from optdash.config import settings

_conn: duckdb.DuckDBPyConnection | None = None


def startup() -> duckdb.DuckDBPyConnection:
    """Create in-process DuckDB connection and register Parquet view."""
    global _conn
    _conn = duckdb.connect(database=":memory:", read_only=False)
    _conn.execute("PRAGMA threads=4")
    _conn.execute("PRAGMA memory_limit='2GB'")
    _register_views(_conn)
    logger.info("DuckDB gateway started — data root: {}", settings.DATA_ROOT)
    return _conn


def _register_views(conn: duckdb.DuckDBPyConnection) -> None:
    """Register Parquet glob as a DuckDB view."""
    data_root = Path(settings.DATA_ROOT)
    if not data_root.exists():
        logger.warning("DATA_ROOT does not exist: {} — views not registered", data_root)
        return

    parquet_glob = str(data_root / "**" / "*.parquet")
    try:
        conn.execute(f"""
            CREATE OR REPLACE VIEW options_data AS
            SELECT * FROM read_parquet('{parquet_glob}', hive_partitioning=true, union_by_name=true)
        """)
        logger.info("options_data view registered — glob: {}", parquet_glob)
    except Exception as e:
        logger.error("Failed to register Parquet view: {}", e)


def get_conn() -> duckdb.DuckDBPyConnection:
    if _conn is None:
        raise RuntimeError("DuckDB not initialized. Call startup() first.")
    return _conn


def shutdown() -> None:
    global _conn
    if _conn:
        _conn.close()
        _conn = None
        logger.info("DuckDB gateway shutdown")
