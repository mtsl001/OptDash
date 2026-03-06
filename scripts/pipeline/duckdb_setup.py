"""
duckdb_setup.py — Compatibility shim for pipeline scripts.

gap_fill.py and run_pipeline.py both import safe_refresh_views() after each
Parquet write cycle.  In the new OptDash architecture, DuckDB views are
managed exclusively by optdash.infrastructure.duckdb_gateway (the API
process) — not by the pipeline process.

This shim provides a no-op safe_refresh_views() so the pipeline can import
it without modification.  The API's DuckDB views are defined as CREATE OR
REPLACE VIEW ... glob(...) — every new query automatically picks up the
latest Parquet files written by the pipeline without any explicit refresh.
"""
import logging

logger = logging.getLogger(__name__)


def safe_refresh_views() -> None:
    """
    No-op in new OptDash.

    Rationale
    ---------
    DuckDB views managed by the API process (optdash.infrastructure.duckdb_gateway)
    glob data/processed/trade_date=*/*.parquet on every query.  No explicit
    view refresh is needed between pipeline writes.

    The pipeline process intentionally does NOT open the DuckDB .db file to
    avoid WAL conflicts with the API process.

    Extension hook
    --------------
    If you ever need the pipeline to signal the API to reload (e.g. after a
    schema migration), replace this body with an IPC/HTTP call:

        import httpx
        httpx.post("http://localhost:8000/internal/refresh-views", timeout=5)
    """
    logger.debug("safe_refresh_views: no-op (DuckDB managed by API process)")
