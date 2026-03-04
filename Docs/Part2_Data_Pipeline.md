# OptDash — Part 2: Data Pipeline & DuckDB Gateway

---

## 1. Overview

OptDash does not connect to a live broker API during analytics. Instead, it reads from pre-built **Parquet snapshot files** that are deposited into a configurable `DATA_ROOT` directory by an external feed process. DuckDB provides a zero-copy columnar SQL interface over these files via an in-memory view.

This design decouples the data feed from the analytics engine — OptDash never crashes due to broker API issues, and all analytics remain fully testable with historical data.

---

## 2. Data Root Structure

Parquet files are expected to follow a **Hive-style partition layout**:

```
DATA_ROOT/
├── trade_date=2026-03-04/
│   ├── NIFTY_09:15.parquet
│   ├── NIFTY_09:20.parquet
│   ├── BANKNIFTY_09:15.parquet
│   └── ...
├── trade_date=2026-03-03/
│   └── ...
└── ...
```

> The glob pattern `DATA_ROOT/**/*.parquet` matches all files recursively. DuckDB's `hive_partitioning=true` automatically parses `trade_date` from the directory name as a column.

---

## 3. DuckDB Gateway (`pipeline/duckdb_gateway.py`)

### 3.1 Connection Strategy

```python
_conn = duckdb.connect(database=":memory:", read_only=False)
_conn.execute("PRAGMA threads=4")
_conn.execute("PRAGMA memory_limit='2GB'")
```

- **In-memory only**: No DuckDB file is written — all data lives in RAM
- **`read_only=False`**: Required for `CREATE OR REPLACE VIEW` DDL
- **4 threads**: Parallelises columnar scans across CPU cores
- **2GB memory limit**: Prevents runaway queries from OOM-killing the process

### 3.2 View Registration

```python
conn.execute(
    "CREATE OR REPLACE VIEW options_data AS "
    "SELECT * FROM read_parquet($1, hive_partitioning=true, union_by_name=true)",
    [parquet_glob],
)
```

**Key parameters:**
- `$1` parameter binding — path never interpolated into SQL string (prevents injection)
- `hive_partitioning=true` — DuckDB auto-detects `trade_date=YYYY-MM-DD` directories
- `union_by_name=true` — Parquet files with different column sets are schema-merged by name; missing columns become NULL instead of causing errors (critical for optional `vex`/`cex` columns)
- `CREATE OR REPLACE` — safe to re-register if `DATA_ROOT` changes without restart

### 3.3 Lifecycle

```
startup()  ──▶  _conn created + view registered
               │
               ▼
           API and Scheduler both call get_conn()
               │
               ▼
 shutdown()  ──▶  _conn.close() + _conn = None
```

### 3.4 Connection Sharing

The single `_conn` instance is shared between:
- **FastAPI API layer** (`deps.py` wires `app.state.duck = get_duck_conn()`)
- **APScheduler tick** (passed directly to all analytics and AI functions)

This guarantees both layers always read the same live Parquet dataset.

> **Important**: DuckDB in-memory connections are NOT thread-safe by default. APScheduler uses `max_instances=1` and `coalesce=True` to ensure only one tick runs at a time. The FastAPI async workers share the connection but DuckDB's internal GIL-like locking handles concurrent read queries safely.

---

## 4. Ingestion Utilities (`pipeline/ingestion.py`)

These are pure read-only helpers that resolve time/date coordinates from `options_data`.

### 4.1 `get_latest_snap(conn, trade_date, underlying) → str | None`

Returns the most recent `snap_time` for a given date and underlying.

```sql
SELECT MAX(snap_time) AS latest
FROM options_data
WHERE trade_date = ? AND underlying = ?
```

Used by the WebSocket endpoint and frontend to auto-select the latest available snap.

### 4.2 `get_available_dates(conn, underlying) → list[str]`

Returns all distinct `trade_date` values in descending order.

```sql
SELECT DISTINCT trade_date
FROM options_data
WHERE underlying = ?
ORDER BY trade_date DESC
```

### 4.3 `get_snap_times(conn, trade_date, underlying) → list[str]`

Returns all snap times for a day in ascending order. Used by the frontend timeline slider.

```sql
SELECT DISTINCT snap_time
FROM options_data
WHERE trade_date = ? AND underlying = ?
ORDER BY snap_time ASC
```

### 4.4 `get_available_underlyings(conn, trade_date) → list[str]`

Returns all underlyings available on a given trade date.

```sql
SELECT DISTINCT underlying
FROM options_data
WHERE trade_date = ?
ORDER BY underlying
```

### 4.5 `_safe_query(conn, sql, params) → list[dict]`

Executes a DuckDB query with graceful handling of missing optional columns.

```python
try:
    result = conn.execute(sql, params or [])
    cols = [d[0] for d in result.description]
    return [dict(zip(cols, row)) for row in result.fetchall()]
except Exception as e:
    if "does not exist" in str(e).lower() or "catalog error" in str(e).lower():
        return []   # Optional column missing in older Parquet files
    raise           # Re-raise unexpected errors
```

This pattern is used by `vex_cex.py` to handle Parquet files that predate the `vex`/`cex` column additions.

---

## 5. Pipeline Scheduler (`pipeline/scheduler.py`)

### 5.1 Job Configuration

```python
_scheduler.add_job(
    func=lambda: _run_tick(conn, jconn),
    trigger=CronTrigger(
        minute="*/5",        # Every 5 minutes
        hour="9-15",         # 09:00 through 15:59 (covers 09:15 – 15:30)
        day_of_week="mon-fri",
        timezone=IST,
    ),
    id="market_tick",
    max_instances=1,         # Prevents overlapping ticks
    coalesce=True,           # Merges missed ticks into single catch-up
)
```

### 5.2 Market Hours Guard

Even though the CronTrigger limits firing to `hour=9-15`, an additional runtime check prevents processing outside official market hours:

```python
def _is_market_hours(now: datetime) -> bool:
    t = now.time()
    open_h, open_m   = map(int, settings.MARKET_OPEN.split(":"))
    close_h, close_m = map(int, settings.MARKET_CLOSE.split(":"))
    return time(open_h, open_m) <= t <= time(close_h, close_m)
```

Default: `MARKET_OPEN=09:15`, `MARKET_CLOSE=15:30`.

### 5.3 Tick Execution Order

Each tick executes the following steps in order. Each step is individually wrapped in `try/except` so a failure in one step never prevents subsequent steps from running:

```
_run_tick(conn, jconn)
    │
    ├─ Step 1: track_open_positions()
    │     For every ACCEPTED trade:
    │     ├─ Fetch current LTP, IV, Greeks from options_data
    │     ├─ Compute PnL (vs actual_entry_price)
    │     ├─ Compute theta-adjusted SL
    │     ├─ Compute trailing stop
    │     ├─ Check IV crush severity
    │     ├─ Run environment gate check
    │     ├─ Insert position_snap
    │     └─ Auto-close if SL/Target/Gate/IV triggered
    │
    ├─ Step 2: track_shadow_positions()
    │     For every shadow (rejected) trade:
    │     ├─ Fetch current LTP from options_data
    │     └─ Compute counterfactual PnL
    │
    ├─ Step 3: expire_stale_recommendations()
    │     For every GENERATED trade older than AI_EXPIRY_MAX_SNAPS:
    │     └─ Update status to EXPIRED
    │
    ├─ Step 4: generate_recommendation() × 5 underlyings
    │     For NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50:
    │     ├─ Skip if open/pending trade exists
    │     ├─ Run full analytics pipeline
    │     └─ Issue trade card if all checks pass
    │
    ├─ Step 5a: eod_force_close()         [fires at EOD_FORCE_CLOSE_TIME exactly]
    │     Close all ACCEPTED trades at market price
    │
    └─ Step 5b: finalize_all_shadows()    [fires at EOD_SWEEP_TIME exactly]
          Compute final shadow PnL, assign outcomes
```

### 5.4 EOD Timing

EOD functions use **strict equality** (`==`) not `>=` to fire exactly once:

```python
# Fires at exactly e.g. "15:20" — not at 15:20, 15:25, 15:30...
if snap_time == settings.EOD_FORCE_CLOSE_TIME:
    eod_force_close(conn, jconn, trade_date)

if snap_time == settings.EOD_SWEEP_TIME:
    finalize_all_shadows(conn, jconn, trade_date)
```

Defaults: `EOD_FORCE_CLOSE_TIME=15:25`, `EOD_SWEEP_TIME=15:30`.

---

## 6. Parquet File Refresh

The external feed process writes new Parquet files to `DATA_ROOT` every 5 minutes. DuckDB's `read_parquet()` view re-scans the glob on every query — there is no caching of directory listings. New files become visible to queries immediately without requiring a restart.

```
External Feed (every 5 min)
    │  writes: DATA_ROOT/trade_date=2026-03-04/NIFTY_09:20.parquet
    ▼
DuckDB glob scan (next query)
    │  discovers new file automatically
    ▼
Analytics sees latest snap
```

---

## 7. Error Handling Strategy

| Scenario | Behaviour |
|---|---|  
| `DATA_ROOT` does not exist at startup | Warning logged, view NOT registered; all queries return empty results |
| New Parquet file appears mid-session | Automatically picked up on next DuckDB query |
| Parquet file missing optional column (`vex`) | `union_by_name=true` fills NULL; `_safe_query()` returns `[]` for analytics depending on it |
| DuckDB query raises unexpected exception | Re-raised by `_safe_query()`; caught by analytics function's `except` block; logged as WARNING |
| Scheduler tick takes > 5 min | `max_instances=1` prevents overlap; `coalesce=True` merges missed ticks |
| Pipeline step fails (e.g. `generate_recommendation` error) | Caught per-underlying; next underlying still processes |

---

## 8. Configuration Reference (Pipeline)

| Setting | Default | Description |
|---|---|---|
| `DATA_ROOT` | `data/parquet` | Root directory for Parquet files |
| `JOURNAL_DB_PATH` | `data/journal.db` | SQLite journal path |
| `MARKET_OPEN` | `09:15` | Market open time (IST) |
| `MARKET_CLOSE` | `15:30` | Market close time (IST) |
| `EOD_FORCE_CLOSE_TIME` | `15:25` | Time to force-close all ACCEPTED trades |
| `EOD_SWEEP_TIME` | `15:30` | Time to finalize shadow trades |
