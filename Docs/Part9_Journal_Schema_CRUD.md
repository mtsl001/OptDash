# OptDash — Part 9: Journal Schema & CRUD

The trade journal is OptDash's permanent record of every trade decision and outcome. It lives in SQLite (`data/db/journal.db`) and is the sole source of truth for the AI engine, learning layer, notifications, and EOD reports. DuckDB is never read or written here.

---

## 1. Design Contract

1. **SQLite only.** The journal never reads from DuckDB or Parquet. It stores decisions and outcomes, not market data.
2. **Context manager always.** All callers open the journal via `journal_conn()`. No raw `sqlite3.connect()` outside `schema.py`.
3. **Foreign keys enforced.** `position_snaps` and `shadow_snaps` reference `trades(id)` with `ON DELETE CASCADE`.
4. **WAL mode.** The journal uses `PRAGMA journal_mode=WAL` so the API (reader) and scheduler (writer) do not block each other.
5. **Row factory.** All connections use `conn.row_factory = sqlite3.Row` so rows are accessible as dicts.

---

## 2. File Map

```
optdash/journal/
    __init__.py
    schema.py     ← DDL, init_db(), run_migrations(), journal_conn() context manager
    trades.py     ← Trade CRUD: insert, get, accept, reject, expire, close
    snaps.py      ← Position snap insert/query, consecutive NOGO counter
    shadow.py     ← Shadow snap insert/query, shadow outcome update
```

---

## 3. `journal/schema.py`

### 3.1 Context Manager

All callers use this. It handles `commit` on success and `rollback` on exception.

```python
from contextlib import contextmanager
import sqlite3
from optdash.config import settings

@contextmanager
def journal_conn():
    conn = sqlite3.connect(str(settings.JOURNAL_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

Usage throughout the scheduler:

```python
# scheduler.py tick()
with journal_conn() as jconn:
    tracker.expire_stale_recommendations(jconn, trade_date, snap_time)
    tracker.track_open_positions(conn, jconn, trade_date, snap_time)
    shadowtracker.track_shadow_positions(conn, jconn, trade_date, snap_time)
    recommender.generate_recommendation(conn, jconn, trade_date, snap_time, underlying)
```

### 3.2 `init_db(conn)`

Creates all three tables if they do not exist. Called once at API startup via the `lifespan` handler.

```python
def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    conn.commit()
```

### 3.3 `run_migrations(conn)`

Applies any `ALTER TABLE` migrations needed as the schema evolves. Each migration is guarded by a `PRAGMA user_version` check so it is idempotent.

```python
def run_migrations(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        # future migrations applied here
        conn.execute("PRAGMA user_version = 1")
    conn.commit()
```

---

## 4. Table: `trades`

Every trade decision — from `GENERATED` through `CLOSED` — is a single row.

### 4.1 DDL

```sql
CREATE TABLE IF NOT EXISTS trades (
    -- Identity
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date       TEXT    NOT NULL,             -- YYYY-MM-DD
    created_at       TEXT    NOT NULL,             -- ISO 8601, snap when GENERATED

    -- Instrument
    underlying       TEXT    NOT NULL,             -- NIFTY | BANKNIFTY | ...
    direction        TEXT    NOT NULL,             -- CE | PE
    expiry_date      TEXT    NOT NULL,             -- YYYY-MM-DD
    dte              INTEGER NOT NULL,
    expiry_tier      TEXT    NOT NULL,             -- TIER1 | TIER2 | TIER3
    strike           REAL    NOT NULL,
    option_type      TEXT    NOT NULL,             -- CE | PE
    lot_size         INTEGER NOT NULL,
    qty_lots         INTEGER NOT NULL DEFAULT 1,

    -- Entry (NULL until ACCEPTED)
    entry_snap_time  TEXT,
    entry_premium    REAL,
    entry_spot       REAL,
    entry_iv         REAL,
    entry_delta      REAL,
    entry_theta      REAL,
    entry_gamma      REAL,
    entry_vega       REAL,

    -- Exit (NULL until CLOSED)
    exit_snap_time   TEXT,
    exit_premium     REAL,
    exit_spot        REAL,

    -- Risk levels set at generation
    sl               REAL,                        -- initial SL price (entry * (1 - AI_SL_PCT))
    target           REAL,                        -- target price (entry * AI_TARGET_MULT)

    -- Lifecycle
    status           TEXT    NOT NULL DEFAULT 'GENERATED',
    exit_reason      TEXT,                        -- ExitReason enum value; NULL until CLOSED
    rejection_reason TEXT,                        -- RejectionReason enum; NULL unless REJECTED
    shadow_outcome   TEXT,                        -- ShadowOutcome; NULL until EOD finalize

    -- Outcome (NULL until CLOSED)
    pnl_pts          REAL,
    pnl_pct          REAL,

    -- Context at generation
    confidence       INTEGER,
    gate_score       INTEGER,
    session          TEXT,                        -- MarketSession enum value
    narrative        TEXT,                        -- template narrative string
    signals          TEXT,                        -- JSON array of signal strings
    conditions       TEXT                         -- JSON snapshot of gate conditions dict
);

CREATE INDEX IF NOT EXISTS idx_trades_date
    ON trades (trade_date);

CREATE INDEX IF NOT EXISTS idx_trades_status
    ON trades (status);

CREATE INDEX IF NOT EXISTS idx_trades_underlying_status
    ON trades (underlying, status);
```

### 4.2 Column Notes

| Column | Populated when | Notes |
|---|---|---|
| `id` | INSERT | Auto-assigned |
| `created_at` | GENERATED | ISO 8601 of the generating snap |
| `entry_*` | ACCEPTED | All entry greeks captured at accept time |
| `exit_*` | CLOSED | Actual exit values |
| `sl`, `target` | GENERATED | Locked at generation, never updated |
| `shadow_outcome` | EOD finalize | Only meaningful for REJECTED/EXPIRED rows |
| `pnl_pts` | CLOSED | `exit_premium − entry_premium` |
| `pnl_pct` | CLOSED | `pnl_pts / entry_premium * 100` |
| `signals` | GENERATED | JSON array e.g. `["S1_VCOC_BULL", "S3_PCR_DIV"]` |
| `conditions` | GENERATED | Full gate `conditions` dict serialised to JSON |

---

## 5. Table: `position_snaps`

Per-snap telemetry for every **ACCEPTED** trade, written every 5 minutes by `tracker.py`.

### 5.1 DDL

```sql
CREATE TABLE IF NOT EXISTS position_snaps (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id             INTEGER NOT NULL
                             REFERENCES trades(id) ON DELETE CASCADE,
    trade_date           TEXT    NOT NULL,
    snap_time            TEXT    NOT NULL,

    -- Market values
    ltp                  REAL,
    spot                 REAL,
    iv                   REAL,
    delta                REAL,
    theta                REAL,

    -- P&L
    pnl_pts              REAL,
    pnl_pct              REAL,

    -- Gate re-evaluation
    gate_score           INTEGER,
    gate_verdict         TEXT,             -- GO | WAIT | NOGO

    -- Risk status
    sl_adjusted          REAL,             -- theta-time-adjusted SL at this snap
    theta_clock_status   TEXT,             -- GREEN | YELLOW | RED
    iv_crush_severity    TEXT              -- NONE | LOW | HIGH
);

CREATE INDEX IF NOT EXISTS idx_psnaps_trade
    ON position_snaps (trade_id, snap_time);
```

### 5.2 What Each Snap Captures

Every snap written by `tracker.track_open_positions()` captures the full position health at that moment — current LTP, Greeks, P&L, gate verdict, adjusted SL, and risk-status flags. This time-series is used by:

- The **Position Monitor** panel (theta-SL series and P&L attribution charts)
- `learning/timeanalysis.py` (session-based performance)
- `count_consecutive_nogo()` (exit trigger)

---

## 6. Table: `shadow_snaps`

Per-snap hypothetical tracking for **REJECTED** and **EXPIRED** trades, written every 5 minutes by `shadowtracker.py` until EOD.

### 6.1 DDL

```sql
CREATE TABLE IF NOT EXISTS shadow_snaps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id    INTEGER NOT NULL
                    REFERENCES trades(id) ON DELETE CASCADE,
    trade_date  TEXT    NOT NULL,
    snap_time   TEXT    NOT NULL,

    -- Hypothetical values
    ltp         REAL,
    spot        REAL,
    pnl_pts     REAL,             -- ltp - entry_premium
    pnl_pct     REAL,             -- pnl_pts / entry_premium * 100

    -- Outcome flags
    hit_target  INTEGER DEFAULT 0,  -- 1 if ltp >= trades.target
    hit_sl      INTEGER DEFAULT 0   -- 1 if ltp <= trades.sl
);

CREATE INDEX IF NOT EXISTS idx_ssnaps_trade
    ON shadow_snaps (trade_id, snap_time);
```

### 6.2 EOD Finalization

At 15:25, `eod.finalize_all_shadows()` reads each shadow trade's snaps and assigns `shadow_outcome`:

| Condition | `shadow_outcome` |
|---|---|
| Any snap `hit_target = 1` | `CLEAN_MISS` |
| Any snap `hit_sl = 1`, no target hit | `GOOD_SKIP` |
| `pnl_pct` ended slightly positive but below target | `RISKY_MISS` |
| `pnl_pct` between −2% and +2% at EOD | `BREAKEVEN` |
| Still open (no determination) | `PENDING` (intraday only) |

---

## 7. `journal/trades.py` — Trade CRUD

### 7.1 Insert

```python
def insert_trade(conn: sqlite3.Connection, data: dict) -> int:
    """
    Insert a GENERATED trade. Returns the new trade id.
    data keys: trade_date, created_at, underlying, direction, expiry_date, dte,
               expiry_tier, strike, option_type, lot_size, qty_lots,
               sl, target, confidence, gate_score, session,
               narrative, signals (JSON str), conditions (JSON str)
    """
```

### 7.2 Reads

```python
def get_trade(conn, trade_id: int) -> dict | None
    # Full row for one trade. Returns None if not found.

def get_trades_by_date(conn, trade_date: str) -> list[dict]
    # All trades for a trade_date, any status.

def get_open_trades(conn, trade_date: str) -> list[dict]
    # WHERE status = 'ACCEPTED' AND trade_date = ?
    # Used by tracker.track_open_positions()

def get_generated_trades(conn, trade_date: str) -> list[dict]
    # WHERE status = 'GENERATED' AND trade_date = ?
    # Used by tracker.expire_stale_recommendations()

def get_closed_trades(conn, lookback_days: int = 30) -> list[dict]
    # WHERE status = 'CLOSED' AND trade_date >= today - lookback_days
    # Used by learning/stats.py, calibration.py, etc.

def get_shadow_trades(conn, trade_date: str) -> list[dict]
    # WHERE status IN ('REJECTED', 'EXPIRED')
    #   AND shadow_outcome = 'PENDING'
    #   AND trade_date = ?
    # Used by shadowtracker.track_shadow_positions()

def has_active_trade(conn, underlying: str) -> bool
    # WHERE underlying = ? AND status IN ('GENERATED', 'ACCEPTED')
    # Dedup check in recommender.generate_recommendation()
```

### 7.3 State Transitions

```python
def accept_trade(
    conn, trade_id: int,
    entry_snap_time: str,
    entry_premium: float,
    entry_spot: float,
    entry_iv: float,
    entry_delta: float,
    entry_theta: float,
    entry_gamma: float,
    entry_vega: float,
) -> None:
    # UPDATE trades SET status='ACCEPTED', entry_snap_time=?, entry_premium=?,
    #   entry_spot=?, entry_iv=?, entry_delta=?, entry_theta=?,
    #   entry_gamma=?, entry_vega=? WHERE id=?

def reject_trade(
    conn, trade_id: int,
    reason: str,          # RejectionReason enum value
) -> None:
    # UPDATE trades SET status='REJECTED', rejection_reason=? WHERE id=?

def expire_trade(conn, trade_id: int) -> None:
    # UPDATE trades SET status='EXPIRED' WHERE id=?
    # Called when GENERATED trade not actioned within AI_EXPIRY_MAX_SNAPS (default 2)

def close_trade(
    conn, trade_id: int,
    exit_snap_time: str,
    exit_premium: float,
    exit_spot: float,
    exit_reason: str,     # ExitReason enum value
    pnl_pts: float,
    pnl_pct: float,
) -> None:
    # UPDATE trades SET status='CLOSED', exit_snap_time=?, exit_premium=?,
    #   exit_spot=?, exit_reason=?, pnl_pts=?, pnl_pct=? WHERE id=?

def update_shadow_outcome(
    conn, trade_id: int,
    outcome: str,         # ShadowOutcome enum value
) -> None:
    # UPDATE trades SET shadow_outcome=? WHERE id=?
    # Called by eod.finalize_all_shadows()
```

---

## 8. `journal/snaps.py` — Position Snap CRUD

```python
def insert_position_snap(
    conn: sqlite3.Connection,
    trade_id: int,
    snap: dict,
) -> None:
    """
    snap keys: trade_date, snap_time, ltp, spot, iv, delta, theta,
               pnl_pts, pnl_pct, gate_score, gate_verdict,
               sl_adjusted, theta_clock_status, iv_crush_severity
    """

def get_position_snaps(
    conn: sqlite3.Connection,
    trade_id: int,
) -> list[dict]:
    # SELECT * FROM position_snaps WHERE trade_id=? ORDER BY snap_time ASC

def get_latest_position_snap(
    conn: sqlite3.Connection,
    trade_id: int,
) -> dict | None:
    # SELECT * FROM position_snaps WHERE trade_id=?
    # ORDER BY snap_time DESC LIMIT 1

def count_consecutive_nogo(
    conn: sqlite3.Connection,
    trade_id: int,
) -> int:
    """
    Count the number of most-recent consecutive NOGO gate verdicts.
    Used by tracker.py to trigger GATE_NOGO exit when count
    >= settings.GATE_SUSTAINED_NOGO_SNAPS (default 2).
    """
    snaps = get_position_snaps(conn, trade_id)  # ordered ASC
    count = 0
    for snap in reversed(snaps):
        if snap["gate_verdict"] == "NOGO":
            count += 1
        else:
            break
    return count
```

---

## 9. `journal/shadow.py` — Shadow Snap CRUD

```python
def insert_shadow_snap(
    conn: sqlite3.Connection,
    trade_id: int,
    snap: dict,
) -> None:
    """
    snap keys: trade_date, snap_time, ltp, spot,
               pnl_pts, pnl_pct, hit_target (int), hit_sl (int)
    """

def get_shadow_snaps(
    conn: sqlite3.Connection,
    trade_id: int,
) -> list[dict]:
    # SELECT * FROM shadow_snaps WHERE trade_id=? ORDER BY snap_time ASC

def get_latest_shadow_snap(
    conn: sqlite3.Connection,
    trade_id: int,
) -> dict | None:
    # SELECT * FROM shadow_snaps WHERE trade_id=?
    # ORDER BY snap_time DESC LIMIT 1
```

---

## 10. Status Transition Map

```
GENERATED
    ├── (user accepts)         →  ACCEPTED
    ├── (user rejects)         →  REJECTED  → shadow tracking → EOD finalize
    └── (2 snaps unactioned)   →  EXPIRED   → shadow tracking → EOD finalize

ACCEPTED
    ├── (SL hit)               →  CLOSED  (exit_reason=SL_HIT)
    ├── (theta SL hit)         →  CLOSED  (exit_reason=THETA_SL_HIT)
    ├── (target hit)           →  CLOSED  (exit_reason=TARGET_HIT)
    ├── (manual close)         →  CLOSED  (exit_reason=MANUAL_EXIT)
    ├── (sustained NOGO)       →  CLOSED  (exit_reason=GATE_NOGO)
    ├── (IV crush)             →  CLOSED  (exit_reason=IV_CRUSH)
    └── (15:20 EOD sweep)      →  CLOSED  (exit_reason=EOD_FORCE)
```

The `AI_EXPIRY_MAX_SNAPS` config value (default 2) controls how many 5-minute snaps a GENERATED trade may remain unactioned before being auto-expired.

---

## 11. Enums Used in Journal Columns

| Column | Enum class | Values |
|---|---|---|
| `status` | `TradeStatus` | `GENERATED`, `ACCEPTED`, `REJECTED`, `EXPIRED`, `CLOSED` |
| `exit_reason` | `ExitReason` | `SL_HIT`, `THETA_SL_HIT`, `TARGET_HIT`, `MANUAL_EXIT`, `GATE_NOGO`, `IV_CRUSH`, `EOD_FORCE` |
| `rejection_reason` | `RejectionReason` | `MISSED_ENTRY`, `LOW_CONFIDENCE`, `NEWS_EVENT`, `ALREADY_IN_TRADE`, `RISK_OFF`, `MANUAL_OVERRIDE` |
| `shadow_outcome` | `ShadowOutcome` | `PENDING`, `CLEAN_MISS`, `GOOD_SKIP`, `RISKY_MISS`, `BREAKEVEN` |
| `direction` | `DirectionStr` | `CE`, `PE` |
| `gate_verdict` | `GateVerdict` | `GO`, `WAIT`, `NOGO` |
| `session` | `MarketSession` | `OPENING_DRIVE`, `MORNING_TREND`, `MIDDAY_CHOP`, `AFTERNOON_DRIVE`, `CLOSING_CRUSH` |
| `theta_clock_status` | (inline) | `GREEN`, `YELLOW`, `RED` |
| `iv_crush_severity` | `IVCrushSeverity` | `NONE`, `LOW`, `HIGH` |

All values are stored as plain `TEXT` — enum membership is enforced in application code, not as SQL constraints, to keep schema migrations simple.

---

## 12. Sprint 4 — Files to Create

```
25. optdash/journal/__init__.py
26. optdash/journal/schema.py
27. optdash/journal/trades.py
28. optdash/journal/snaps.py
29. optdash/journal/shadow.py
```

### Checkpoint

```bash
python -c "
import sqlite3, tempfile, os
from optdash.journal.schema import init_db, run_migrations
from optdash.journal.trades import insert_trade, get_trade, has_active_trade
from optdash.journal.snaps  import insert_position_snap, count_consecutive_nogo
from optdash.journal.shadow import insert_shadow_snap
import json, datetime

tmp = tempfile.mktemp(suffix='.db')
conn = sqlite3.connect(tmp)
conn.row_factory = sqlite3.Row
init_db(conn)
run_migrations(conn)

# Insert a GENERATED trade
tid = insert_trade(conn, {
    'trade_date':   '2026-02-28',
    'created_at':   '2026-02-28T10:15:00',
    'underlying':   'NIFTY',
    'direction':    'CE',
    'expiry_date':  '2026-03-06',
    'dte':          6,
    'expiry_tier':  'TIER1',
    'strike':       22500.0,
    'option_type':  'CE',
    'lot_size':     75,
    'qty_lots':     1,
    'sl':           72.0,
    'target':       180.0,
    'confidence':   72,
    'gate_score':   7,
    'session':      'MORNING_TREND',
    'narrative':    'Test trade',
    'signals':      json.dumps(['S1_VCOC_BULL']),
    'conditions':   json.dumps({}),
})
assert tid == 1
assert has_active_trade(conn, 'NIFTY') is True

# Position snap
insert_position_snap(conn, tid, {
    'trade_date': '2026-02-28', 'snap_time': '10:20',
    'ltp': 125.0, 'spot': 22480.0, 'iv': 14.2,
    'delta': 0.45, 'theta': -12.0,
    'pnl_pts': 5.0, 'pnl_pct': 4.2,
    'gate_score': 7, 'gate_verdict': 'GO',
    'sl_adjusted': 71.5, 'theta_clock_status': 'GREEN',
    'iv_crush_severity': 'NONE',
})
assert count_consecutive_nogo(conn, tid) == 0

print('JOURNAL CHECKPOINT PASSED')
conn.close()
os.unlink(tmp)
"
```
