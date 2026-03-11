"""DuckDB gateway -- in-process connection over processed Parquet files.

View scope
----------
Reads ONLY ``data/processed/trade_date=*/`` -- never ``data/raw/``.
The raw subtree has a different schema (no Greeks, no enriched columns)
and including it via union_by_name produces NULL rows for every
analytic column (delta, iv, gex, vex, cex, ...).

File layout expected
--------------------
  data/processed/trade_date=YYYY-MM-DD/
      NIFTY.parquet       <- all snaps for NIFTY on that day
      BANKNIFTY.parquet
      ...

DuckDB extracts ``trade_date`` automatically from the hive partition
directory name.  The ``underlying`` column is embedded in each file by
the writer (optdash/pipeline/writer.py).

Rolling window
--------------
On startup (and on demand via refresh_views), the view is registered
over only the last DUCK_VIEW_LOOKBACK_DAYS calendar days.  This bounds
the number of files DuckDB opens regardless of how long the service has
been running.

View refresh
------------
Call refresh_views(conn) at day rollover so new-day partition directories
become visible without restarting the process.  The scheduler calls this
once per day during the EOD sweep block.

Concurrency (P1-9 / P1-P2-4)
------------------------------
``_view_lock`` is a threading.RLock used as a readers-writer guard.

DuckDB's CREATE OR REPLACE VIEW is internally a DROP + CREATE on the
catalog entry.  A concurrent SELECT that has already resolved the view
name during query planning but not yet executed can receive a
CatalogException ('View not found') when the catalog entry is
momentarily absent.  MVCC protects data rows but NOT catalog entries.

Fix (P1-P2-4): All analytics callers obtain a LockedConn proxy from
get_conn().  Every .execute() / .executemany() call on LockedConn
automatically acquires _view_lock before delegation and releases it in
a finally block.  Protection is structural -- no call site discipline
required.  New analytics functions cannot accidentally skip the lock.

refresh_views() still acquires _view_lock explicitly for the full
DROP+CREATE+validate cycle, blocking all LockedConn.execute() calls
during the catalog swap.  RLock is reentrant so _validate_view_schema()
(called from inside refresh_views while the lock is already held) does
not deadlock.

view_lock() context manager is kept for callers that need an explicit
wider lock scope (e.g. multi-statement transactions).  It is now
redundant for single execute() calls but harmless -- RLock is reentrant.
"""
import threading
import duckdb
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from loguru import logger

from optdash.config import settings

_conn: duckdb.DuckDBPyConnection | None = None
# P1-9 / P1-P2-4: RLock instead of Lock.
# - Exclusive acquisition in refresh_views() guards the DROP+CREATE catalog swap.
# - Automatic acquisition in LockedConn.execute() serialises all SELECT calls.
# - Reentrant so _validate_view_schema() (called inside refresh_views while
#   the lock is held) does not deadlock.
_view_lock = threading.RLock()

IST = ZoneInfo("Asia/Kolkata")
PROCESSED_SUBDIR = "processed"

# Columns that ALL analytics functions depend on being present and non-NULL.
# union_by_name=true fills absent columns with NULL rather than raising, so
# an older Parquet file with a different schema silently corrupts gate scores,
# PnL calculations, and screener results without any error or log entry.
# Validated against the registered view on every startup and EOD refresh so
# schema drift is caught immediately, not at trade time.
#
# Sync rule: this set must mirror PARQUET_SCHEMA field names in writer.py.
#   - s_score: REMOVED (computed live by screener.py, never in Parquet)
#   - bid_qty / ask_qty: ADDED (mapped from total_buy/sell_qty in BQ feed;
#       required by coc.py get_atm_obi/get_futures_obi and pcr.py _smoothed_obi;
#       absence silently zeroes OBI and prevents Gates C3 + C6 from ever firing)
#   - vex / cex: ADDED (Vanna + Charm Exposure; required by vex_cex.py)
#   - oi / volume / dte: ADDED (were in PARQUET_SCHEMA but missing from this set)
REQUIRED_COLUMNS: frozenset[str] = frozenset({
    "trade_date", "snap_time", "underlying", "strike_price", "expiry_date",
    "option_type", "instrument_type", "ltp", "iv", "delta", "theta",
    "gamma", "vega", "spot", "fut_price", "oi", "volume",
    "bid_qty", "ask_qty",       # OBI columns (from total_buy / total_sell qty)
    "gex", "vex", "cex",        # dealer exposure columns
    "expiry_tier", "dte",       # enrichment columns
    # NOT included: s_score (live compute), rho (not in Upstox API)
})


# ---------------------------------------------------------------------------
# P1-P2-4: LockedConn -- thread-safe proxy for DuckDBPyConnection
# ---------------------------------------------------------------------------

class LockedConn:
    """Thin proxy around DuckDBPyConnection that serialises every execute()
    call through _view_lock.

    WHY THIS EXISTS
    ---------------
    FastAPI sync endpoints run on the anyio thread pool (up to 40 workers).
    All workers share the same DuckDB :memory: connection via get_conn().
    DuckDB's in-process connection is not safe for concurrent multi-thread
    access on the same connection object.

    The old pattern -- view_lock() as an opt-in context manager -- required
    every analytics function to explicitly wrap its DuckDB calls.  Any new
    function that forgot it silently raced.  LockedConn makes the protection
    structural: callers cannot call .execute() without going through the lock.

    USAGE
    -----
    Callers obtain a LockedConn from get_conn() and use it exactly like a
    raw DuckDBPyConnection:

        conn = get_conn()            # returns LockedConn
        row  = conn.execute(sql, params).fetchone()

    Existing `with view_lock(): conn.execute(...)` patterns are still correct
    -- RLock is reentrant, so the double-acquire is a no-op.

    WHAT IS PROXIED
    ---------------
    .execute() and .executemany() -- the only entry points used by analytics.
    All other attribute accesses (fetchall, fetchone, description, close,
    context-manager protocol) are delegated to _real unchanged.

    THREAD SAFETY OF _real
    -----------------------
    LockedConn holds _view_lock for the duration of the .execute() call.
    DuckDB executes the query synchronously inside that call, so the
    connection is never accessed concurrently by two threads.
    """

    __slots__ = ("_real",)

    def __init__(self, real: duckdb.DuckDBPyConnection) -> None:
        object.__setattr__(self, "_real", real)

    # ── Locked entry points ────────────────────────────────────────────────

    def execute(self, query: str, parameters=None):
        """Acquire _view_lock, delegate to real .execute(), release on exit."""
        _view_lock.acquire()
        try:
            if parameters is not None:
                return self._real.execute(query, parameters)
            return self._real.execute(query)
        finally:
            _view_lock.release()

    def executemany(self, query: str, parameters=None):
        """Acquire _view_lock, delegate to real .executemany(), release on exit."""
        _view_lock.acquire()
        try:
            if parameters is not None:
                return self._real.executemany(query, parameters)
            return self._real.executemany(query)
        finally:
            _view_lock.release()

    # ── Transparent delegation for all other attributes ───────────────────

    def __getattr__(self, name: str):
        return getattr(object.__getattribute__(self, "_real"), name)

    def __setattr__(self, name: str, value) -> None:
        if name == "_real":
            object.__setattr__(self, name, value)
        else:
            setattr(object.__getattribute__(self, "_real"), name, value)

    def __enter__(self):
        return self._real.__enter__()

    def __exit__(self, *args):
        return self._real.__exit__(*args)

    def __repr__(self) -> str:
        return f"LockedConn({self._real!r})"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@contextmanager
def view_lock():
    """Context manager that holds _view_lock while a block executes.

    P1-9 / P1-P2-4: for single execute() calls, LockedConn.execute()
    already acquires _view_lock automatically -- explicit view_lock() is
    no longer required for single-statement callers.

    Use view_lock() explicitly when you need to hold the lock across
    multiple statements (e.g. a multi-step read-then-write sequence that
    must not be interrupted by a concurrent refresh_views() call).

    RLock is reentrant -- wrapping a LockedConn.execute() inside
    view_lock() is safe and results in two reentrant acquisitions (no
    deadlock).

    Usage::

        with view_lock():
            r1 = get_conn().execute("SELECT ...").fetchone()
            r2 = get_conn().execute("SELECT ...").fetchone()

    All analytics modules that query options_data may use this guard for
    multi-statement sequences.  Single-statement callers need not change.
    """
    _view_lock.acquire()
    try:
        yield
    finally:
        _view_lock.release()


def startup() -> "LockedConn":
    """Create in-process DuckDB connection and register rolling Parquet view.

    Returns a LockedConn proxy so callers (deps.startup) can store it on
    app.state.duck without a second call to get_conn().

    Raises RuntimeError if view registration or schema validation fails on
    startup so the process fails loudly rather than starting in a degraded
    no-analytics state.
    """
    global _conn
    _conn = duckdb.connect(database=":memory:", read_only=False)
    _conn.execute("PRAGMA threads=4")
    _conn.execute("PRAGMA memory_limit='2GB'")
    # raise_on_error=True: startup must fail loudly if Parquet files are
    # corrupted, the view cannot be registered, or required columns are missing.
    # The alternative -- starting with no options_data view or a schema gap --
    # would cause every analytics endpoint to return 500 errors or silent NULLs
    # with no obvious root-cause trail.
    refresh_views(_conn, raise_on_error=True)
    logger.info("DuckDB gateway started -- data root: {}", settings.DATA_ROOT)
    return LockedConn(_conn)


def refresh_views(
    conn,
    raise_on_error: bool = False,
) -> None:
    """Register (or re-register) the rolling window options_data view.

    Uses CREATE OR REPLACE VIEW so it is safe to call at any time.
    Call once per trading day at EOD so the new-day partition directory
    enters the rolling window without requiring a process restart.

    After view registration, _validate_view_schema() checks that all
    REQUIRED_COLUMNS are present.  On startup (raise_on_error=True) a
    schema gap crashes the process; on EOD refresh it logs an error only.

    P1-9 / P1-P2-4: _view_lock is acquired exclusively for the full
    CREATE OR REPLACE VIEW + validate cycle.  Concurrent LockedConn.execute()
    callers will block until this returns, preventing the CatalogException
    'View not found' that occurs when a SELECT plans against the view name
    during the internal DROP+CREATE window.

    conn may be a LockedConn proxy or a bare DuckDBPyConnection.  The lock
    is acquired explicitly here, so we unwrap to the real connection to
    avoid a reentrant double-lock on the execute() call inside refresh_views.

    Parameters
    ----------
    conn:           Active DuckDB connection (LockedConn or raw).
    raise_on_error: If True, re-raise any exception after logging it.
                    Pass True on startup (fail-fast); use the default
                    False for intra-day EOD refreshes so a bad day-
                    rollover file doesn't crash the running process.
    """
    # Unwrap LockedConn so the raw connection is used inside the already-held
    # _view_lock.  RLock is reentrant, so calling conn.execute() via LockedConn
    # while _view_lock is held would also work -- but unwrapping avoids the
    # extra reentrant acquire/release cycle per SQL statement.
    real = conn._real if isinstance(conn, LockedConn) else conn

    data_root = Path(settings.DATA_ROOT)
    processed = data_root / PROCESSED_SUBDIR

    if not data_root.exists():
        logger.warning(
            "DATA_ROOT does not exist: {} -- view not registered", data_root
        )
        return

    globs = _build_rolling_globs(processed, settings.DUCK_VIEW_LOOKBACK_DAYS)
    if not globs:
        logger.warning(
            "No processed Parquet directories found under {} -- view not registered",
            processed,
        )
        return

    # P1-9 / P1-P2-4: _view_lock acquired exclusively for the full
    # DROP+CREATE+validate cycle.  All LockedConn.execute() callers block here
    # until refresh_views returns, preventing CatalogException on concurrent
    # queries that plan against the view name during the swap window.
    with _view_lock:
        try:
            real.execute(
                "CREATE OR REPLACE VIEW options_data AS "
                "SELECT * FROM read_parquet($1, hive_partitioning=true, union_by_name=true)",
                [globs],
            )
            logger.info(
                "options_data view registered -- {} day partition(s) in rolling window",
                len(globs),
            )
            # Validate schema immediately after registration so missing columns
            # from older Parquet files are caught here, not silently at query time.
            _validate_view_schema(real, raise_on_error=raise_on_error)
        except Exception as e:
            logger.error("Failed to register Parquet view: {}", e)
            if raise_on_error:
                raise


def _validate_view_schema(
    conn: duckdb.DuckDBPyConnection,
    raise_on_error: bool = False,
) -> None:
    """Verify all REQUIRED_COLUMNS exist in the registered options_data view.

    Catches column renames and removals that union_by_name would silently
    fill with NULLs.  Does NOT detect per-file partial NULLs -- that
    requires canonical PyArrow schema enforcement in writer.py (issue W-1).

    On startup (raise_on_error=True) a missing column raises RuntimeError
    so the process fails loudly.  On EOD refresh (raise_on_error=False)
    the error is logged without interrupting the running session.

    Called from inside refresh_views() while _view_lock is held with a
    bare DuckDBPyConnection (not LockedConn) -- safe because the lock is
    already held exclusively by the caller.
    """
    try:
        schema_rows = conn.execute("DESCRIBE options_data").fetchall()
        found   = {r[0] for r in schema_rows}
        missing = REQUIRED_COLUMNS - found
        if missing:
            msg = (
                f"options_data view is missing required columns: {sorted(missing)}. "
                "Older Parquet files may be silently NULLing analytics columns. "
                "Run writer.py schema migration or remove stale partition directories."
            )
            logger.error(msg)
            if raise_on_error:
                raise RuntimeError(msg)
        else:
            logger.debug(
                "options_data schema OK -- all {} required columns present.",
                len(REQUIRED_COLUMNS),
            )
    except RuntimeError:
        raise
    except Exception as e:
        logger.error("options_data schema validation query failed: {}", e)
        if raise_on_error:
            raise


def _build_rolling_globs(processed_root: Path, lookback_days: int) -> list[str]:
    """Return per-day *.parquet glob strings for the rolling lookback window.

    Only includes date directories that actually exist on disk -- the
    view registration never fails on a fresh install with no data.

    Uses IST-aware date so the correct calendar day is used regardless of
    the server OS timezone (avoids off-by-one at the 00:00-05:29 UTC window
    when OS timezone is UTC but the trading app runs on IST).
    """
    today = datetime.now(IST).date()   # IST-aware, not system-local
    globs: list[str] = []
    for i in range(lookback_days):
        d       = today - timedelta(days=i)
        day_dir = processed_root / f"trade_date={d.strftime('%Y-%m-%d')}"
        if day_dir.exists():
            globs.append(str(day_dir / "*.parquet"))
    return globs


def get_conn() -> LockedConn:
    """Return the shared DuckDB connection as a thread-safe LockedConn proxy.

    P1-P2-4: returns LockedConn instead of the raw DuckDBPyConnection.
    Every .execute() call on the returned object automatically acquires
    _view_lock, serialising concurrent analytics calls without requiring
    any discipline at call sites.

    Callers use this exactly like a raw DuckDBPyConnection:
        conn = get_conn()
        row  = conn.execute("SELECT ...", [params]).fetchone()
    """
    if _conn is None:
        raise RuntimeError("DuckDB not initialized. Call startup() first.")
    return LockedConn(_conn)


def shutdown() -> None:
    global _conn
    if _conn:
        _conn.close()
        _conn = None
        logger.info("DuckDB gateway shutdown")
