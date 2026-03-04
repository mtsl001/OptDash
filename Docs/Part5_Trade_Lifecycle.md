# OptDash — Part 5: Trade Lifecycle

A trade in OptDash moves through a strict, one-way state machine. Every transition is logged to SQLite, auditable, and irreversible. The journal is the single source of truth for all open and historical positions.

---

## 1. State Machine

```
GENERATED ──► ACCEPTED ──► CLOSED
         │                    ▲
         ├──► REJECTED         │
         │                     │
         └──► EXPIRED ─────────┘  (shadow only)
```

| Status | Description | Set By |
|---|---|---|
| `GENERATED` | Recommendation written; awaiting user action | `recommender.generate_recommendation()` |
| `ACCEPTED` | User accepted; position is live | `POST /ai/accept` |
| `REJECTED` | User rejected before entry | `POST /ai/reject` |
| `EXPIRED` | Not actioned within `EXPIRY_MAX_SNAPS` snaps | `tracker.expire_stale_recommendations()` |
| `CLOSED` | Position closed (SL / target / manual / EOD) | `tracker.track_open_positions()` or `eod.eod_force_close()` |

Only one `ACCEPTED` trade per underlying is permitted at any time. The recommender guards this at generation time.

---

## 2. Journal Schema (`journal/schema.py`)

The SQLite journal has three primary tables: `trades`, `position_snaps`, and `shadow_snaps`.

### 2.1 `trades` Table

| Column | Type | Description |
|---|---|---|
| `trade_id` | TEXT PK | UUID generated at creation |
| `underlying` | TEXT | e.g., `NIFTY`, `BANKNIFTY` |
| `trade_date` | TEXT | ISO date `YYYY-MM-DD` |
| `direction` | TEXT | `CE` or `PE` |
| `strike_price` | REAL | Recommended strike |
| `expiry_date` | TEXT | Option expiry |
| `dte` | INTEGER | Days to expiry at entry |
| `entry_premium` | REAL | LTP at time of recommendation |
| `sl` | REAL | Initial stop-loss (`entry × (1 - AI_SL_PCT)`) |
| `target` | REAL | Initial target (`entry × AI_TARGET_MULT`) |
| `confidence` | INTEGER | 0–100 score at generation |
| `quality_grade` | TEXT | A / B / C / D |
| `gate_score` | INTEGER | Environment gate score at generation |
| `status` | TEXT | `TradeStatus` enum value |
| `exit_reason` | TEXT | `ExitReason` enum value (nullable) |
| `exit_premium` | REAL | LTP at close (nullable) |
| `pnl_pts` | REAL | `exit_premium - entry_premium` (nullable) |
| `pnl_pct` | REAL | `pnl_pts / entry_premium * 100` (nullable) |
| `accepted_at` | TEXT | ISO timestamp of user accept |
| `closed_at` | TEXT | ISO timestamp of close |
| `narrative` | TEXT | Full narrative text |
| `preflight_failures` | TEXT | JSON list of failure codes |
| `signals` | TEXT | JSON dict of directional signals |
| `snap_count` | INTEGER | Number of snaps tracked (ACCEPTED only) |

### 2.2 `position_snaps` Table

One row per scheduler tick for each `ACCEPTED` trade.

| Column | Type | Description |
|---|---|---|
| `snap_id` | TEXT PK | UUID |
| `trade_id` | TEXT FK | References `trades.trade_id` |
| `snap_time` | TEXT | Snap timestamp |
| `current_ltp` | REAL | Current option premium |
| `theta_sl` | REAL | Dynamic SL at this snap |
| `gate_score` | INTEGER | Environment gate at this snap |
| `gate_verdict` | TEXT | `GO` / `WAIT` / `NO-GO` |
| `iv_crush_severity` | TEXT | `NONE` / `LOW` / `HIGH` |
| `pnl_pts` | REAL | Running P&L in points |
| `pnl_pct` | REAL | Running P&L as % of entry |
| `theta_clock` | TEXT | `GREEN` / `YELLOW` / `RED` |
| `trailing_sl_active` | INTEGER | `1` if trailing stop engaged |
| `trailing_sl` | REAL | Trailing SL level (nullable) |

### 2.3 `shadow_snaps` Table

Tracks `REJECTED` and `EXPIRED` trades hypothetically using the same structure as `position_snaps`. Results feed `learning/regret.py` for calibration.

---

## 3. Trade CRUD (`journal/trades.py`)

### 3.1 `create_trade()`

Called exclusively by `recommender.generate_recommendation()`. Writes status `GENERATED`.

```python
def create_trade(
    conn: sqlite3.Connection,
    underlying: str,
    trade_date: str,
    direction: str,           # CE / PE
    strike_price: float,
    expiry_date: str,
    dte: int,
    entry_premium: float,
    sl: float,
    target: float,
    confidence: int,
    quality_grade: str,
    gate_score: int,
    narrative: str,
    signals: dict,
    preflight_failures: list,
) -> str:                     # returns trade_id
```

### 3.2 `accept_trade()`

Called by `POST /ai/accept`. Transitions `GENERATED → ACCEPTED`, records `accepted_at`.

```python
def accept_trade(conn, trade_id: str, snap_time: str) -> None
```

**Guards:**
- Trade must be in `GENERATED` status
- No existing `ACCEPTED` trade for the same underlying

### 3.3 `reject_trade()`

Called by `POST /ai/reject`. Transitions `GENERATED → REJECTED`.

```python
def reject_trade(conn, trade_id: str, reason: RejectionReason) -> None
```

`RejectionReason` values: `MISSED_ENTRY`, `LOW_CONFIDENCE`, `NEWS_EVENT`, `ALREADY_IN_TRADE`, `RISK_OFF`, `MANUAL_OVERRIDE`.

### 3.4 `close_trade()`

Transitions `ACCEPTED → CLOSED`. Records exit data and computes final P&L.

```python
def close_trade(
    conn,
    trade_id: str,
    exit_premium: float,
    exit_reason: ExitReason,
    snap_time: str,
) -> None
```

`ExitReason` values:

| Code | Trigger |
|---|---|
| `SL_HIT` | `current_ltp <= sl` (initial SL backstop) |
| `THETA_SL_HIT` | `current_ltp <= theta_sl` (dynamic SL) |
| `TARGET_HIT` | `current_ltp >= target` |
| `MANUAL_EXIT` | User-initiated via `POST /ai/close-trade` |
| `GATE_NOGO` | Gate verdict `NO-GO` for `GATE_SUSTAINED_NOGO_SNAPS` consecutive snaps |
| `IV_CRUSH` | IV crush severity `HIGH` with vega ≥ threshold |
| `EOD_FORCE` | Forced close at `EOD_SWEEP_TIME` (15:25) |

---

## 4. Recommendation Expiry

Stale `GENERATED` trades expire automatically. This runs as **Step 7** of the scheduler loop.

```python
def expire_stale_recommendations(
    conn: sqlite3.Connection,
    trade_date: str,
    snap_time: str,
) -> None
```

**Logic:**

```python
stale = trades where status == GENERATED
             AND snap_count >= settings.EXPIRY_MAX_SNAPS   # default: 2 snaps = 10 min

for trade in stale:
    update status = EXPIRED
    log.warning(f"Trade {trade_id} expired — not actioned within {EXPIRY_MAX_SNAPS} snaps")
```

An expired trade is immediately picked up by `shadow_tracker.py` for hypothetical outcome tracking.

---

## 5. Full Lifecycle Example

```
09:52:00  recommender.generate_recommendation()
          → trade_id = "abc-123", status = GENERATED
          → entry = 87.50, SL = 52.50, target = 131.25
          → quality = B, confidence = 72, gate = 8/11

09:53:00  User action: POST /ai/accept
          → status = ACCEPTED, accepted_at = 09:53:00

09:53:00 → 10:38:00  tracker.track_open_positions()
          → 9 position_snaps recorded every 5 min
          → theta_sl rising from 52.50 → 54.20 (theta protection increasing)
          → trailing stop activated at 10:15 (20% PnL threshold reached)

10:38:00  current_ltp = 131.60 >= target = 131.25
          → close_trade(exit_reason = TARGET_HIT)
          → status = CLOSED, pnl_pts = +44.10, pnl_pct = +50.4%
          → learning engine updated with win, signal accuracy, confidence calibration
```

---

## 6. API Endpoints (`api/routers/ai.py`)

| Method | Path | Action |
|---|---|---|
| `GET` | `/ai/recommend` | Trigger recommendation for an underlying |
| `POST` | `/ai/accept` | Accept a `GENERATED` trade |
| `POST` | `/ai/reject` | Reject a `GENERATED` trade with reason |
| `POST` | `/ai/close-trade` | Manual close an `ACCEPTED` trade |
| `GET` | `/ai/status/{underlying}` | Get current trade status and live snap |
| `GET` | `/ai/journal` | List all trades (paginated, filterable) |

---

## 7. Learning Feedback Loop

Every closed trade feeds directly into the learning engine:

```
close_trade()
    │
    ├─ learning/stats.py        → update win_rate, avg_pnl, streak
    ├─ learning/signals.py      → per-signal accuracy, decay detection
    ├─ learning/calibration.py  → confidence calibration bands
    ├─ learning/direction.py    → CE/PE directional accuracy
    └─ learning/regret.py       → rejected/expired outcome analysis
```

The updated `win_rate` from `stats.py` feeds back into **Bucket 4** of `compute_confidence()` (see Part 4, Section 3.4) on the next recommendation cycle.

---

## 8. Summary: What Can Close a Trade?

| Exit Reason | Who Triggers | Condition |
|---|---|---|
| `SL_HIT` | Tracker (auto) | `current_ltp ≤ initial_sl` |
| `THETA_SL_HIT` | Tracker (auto) | `current_ltp ≤ theta_sl` (dynamic) |
| `TARGET_HIT` | Tracker (auto) | `current_ltp ≥ target` |
| `MANUAL_EXIT` | User (API) | `POST /ai/close-trade` |
| `GATE_NOGO` | Tracker (auto) | 2 consecutive NO-GO snaps |
| `IV_CRUSH` | Tracker (auto) | IV fell >3% with vega ≥ 0.5 |
| `EOD_FORCE` | EOD sweep | Any open trade at 15:25 |
