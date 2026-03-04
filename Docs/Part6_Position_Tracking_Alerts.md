# OptDash — Part 6: Position Tracking & Alerts

Once a trade is `ACCEPTED`, it enters the live tracking loop. The tracker runs on every scheduler tick (every 5 minutes) and makes automated exit decisions based on dynamic SL, gate deterioration, IV crush, and trailing stop rules.

---

## 1. Live Tracker (`ai/tracker.py`)

### 1.1 Overview

`track_open_positions()` is called as **Step 8** of the scheduler loop — after `expire_stale_recommendations()` (Step 7) and before `generate_recommendation()` (Step 10).

```python
def track_open_positions(
    conn: duckdb.DuckDBPyConnection,   # analytics DuckDB
    jconn: sqlite3.Connection,         # journal SQLite
    trade_date: str,
    snap_time: str,
) -> None
```

### 1.2 Per-Trade Tracking Flow

```
For each ACCEPTED trade:
    │
    ├─ [1] Fetch current_ltp from DuckDB (vw_atm)
    ├─ [2] compute_theta_sl()          → dynamic SL
    ├─ [3] compute_theta_clock()       → GREEN / YELLOW / RED
    ├─ [4] check_iv_crush()            → severity NONE / LOW / HIGH
    ├─ [5] get_environment_score()     → current gate
    ├─ [6] check_trailing_stop()       → update trailing_sl if active
    │
    ├─ [Exit check 1] current_ltp <= theta_sl?            → THETA_SL_HIT
    ├─ [Exit check 2] current_ltp <= initial_sl?          → SL_HIT (backstop)
    ├─ [Exit check 3] current_ltp >= target?              → TARGET_HIT
    ├─ [Exit check 4] Gate sustained NO-GO?               → GATE_NOGO
    ├─ [Exit check 5] IV crush HIGH?                      → IV_CRUSH
    │
    ├─ No exit triggered?
    │   └─ write position_snap()
    │
    └─ Exit triggered?
        └─ close_trade(exit_reason, exit_premium=current_ltp)
```

> Exit checks are evaluated in order. The first triggered condition wins. Only one exit reason is recorded per trade.

---

## 2. Theta SL Engine (`analytics/pnl.py`)

The initial SL is static. The theta SL is dynamic — it rises over time as theta decay is factored in, protecting accrued profit.

### 2.1 Formula

```python
def compute_theta_sl(
    entry_premium: float,
    theta_daily: float,
    t_elapsed_min: int,
    max_loss_pct: float = settings.AI_SL_PCT,   # default 0.40
) -> float:

    sl_base      = entry_premium * max_loss_pct
    theta_decay  = abs(theta_daily) * (t_elapsed_min / 390)  # 390 = market min/day
    sl_adjusted  = sl_base - theta_decay
    return sl_adjusted
```

As time passes, `theta_decay` grows and `sl_adjusted` decreases — meaning the SL floor moves closer to the entry premium, forcing an earlier exit if the position is not moving.

### 2.2 Example SL Progression (entry=100, theta_daily=4.80, AI_SL_PCT=0.40)

| Elapsed Time | theta_decay | sl_base | theta_sl |
|---|---|---|---|
| 0 min (entry) | 0.00 | 40.00 | 40.00 |
| 78 min | 0.96 | 40.00 | 39.04 |
| 195 min | 2.40 | 40.00 | 37.60 |
| 390 min (EOD) | 4.80 | 40.00 | 35.20 |

> `theta_sl` is always the operative SL when it is higher than the initial SL. Both are checked at every snap — whichever is more protective wins.

### 2.3 Theta SL Series

For the position monitor chart, the tracker computes the full `theta_sl` series since entry:

```python
def compute_theta_sl_series(
    entry_premium: float,
    theta_daily: float,
    entry_snap: str,
    all_snaps: list[str],
) -> list[dict]:
    # Returns [{snap_time, theta_sl}, ...] for every snap since entry
    # Used to render the rising SL line on the position chart
```

---

## 3. Theta Clock (`analytics/pnl.py`)

The theta clock compares the remaining theta cost (time decay until expiry) against the remaining profit distance to target.

```python
def compute_theta_clock(
    entry_theta: float,
    dte: int,
    current_ltp: float,
    target: float,
) -> str:

    theta_cost_remaining = abs(entry_theta) * dte
    target_remaining     = target - current_ltp
    ratio = theta_cost_remaining / max(target_remaining, 0.01)

    if ratio <= 0.5:   return "GREEN"    # theta small vs remaining target
    if ratio <= 1.0:   return "YELLOW"   # theta eating into remaining upside
    return "RED"                         # theta will consume target before expiry
```

| Clock | Ratio | Action |
|---|---|---|
| GREEN | ≤ 0.5 | Theta is manageable; hold position |
| YELLOW | 0.5–1.0 | Monitor closely; consider partial exit |
| RED | > 1.0 | Theta will consume the target; immediate exit signal |

The theta clock is stored in every `position_snap` and surfaced in the UI position monitor.

---

## 4. IV Crush Detection

### 4.1 Logic

```python
iv_at_entry   = trade["iv"]               # stored at recommendation time
iv_current    = get_ivr_ivp(conn, ...)["iv"]

iv_change_pct = (iv_current - iv_at_entry) / iv_at_entry * 100   # negative = crush

if   iv_change_pct > -settings.IV_CRUSH_LOW_THRESHOLD:    # default: -1.0
    severity = IVCrushSeverity.NONE
elif iv_change_pct > -settings.IV_CRUSH_HIGH_THRESHOLD:   # default: -3.0
    severity = IVCrushSeverity.LOW
else:
    if vega >= settings.IV_CRUSH_HIGH_VEGA:               # default: 0.5
        severity = IVCrushSeverity.HIGH
    else:
        severity = IVCrushSeverity.LOW
```

### 4.2 Exit Trigger

`IVCrushSeverity.HIGH` triggers an immediate `IV_CRUSH` exit. `LOW` is recorded in the snap but does not exit — it surfaces as a warning in the dashboard and narrative.

| Severity | IV Drop | Vega Guard | Action |
|---|---|---|---|
| `NONE` | < 1% | — | Record, no action |
| `LOW` | 1%–3% | — | Record, warn in UI |
| `HIGH` | > 3% | vega ≥ 0.5 | Exit trade immediately |
| `LOW` (vega bypass) | > 3% | vega < 0.5 | Record as LOW only |

---

## 5. Trailing Stop

### 5.1 Activation

The trailing stop activates once P&L reaches `TRAILING_STOP_ACTIVATION` (default: 20% gain).

```python
pnl_pct = (current_ltp - entry_premium) / entry_premium * 100

if pnl_pct >= settings.TRAILING_STOP_ACTIVATION * 100:     # default: 20.0%
    trailing_sl_active = True
    trailing_sl = max(trailing_sl or 0,
                      current_ltp * (1 - settings.AI_SL_PCT))  # lock in profit floor
```

### 5.2 Behaviour

- `trailing_sl` only moves **upward** — it never decreases once set.
- If `current_ltp` drops below `trailing_sl`, the exit reason is `SL_HIT`.
- The trailing SL supersedes the initial SL once it is the higher value.

| P&L Level | trailing_sl Active | Effective SL |
|---|---|---|
| < 20% gain | No | Initial SL (`entry × 0.60`) |
| 20% gain | Yes — activated | `current × 0.85` (locks 20% floor) |
| 30% gain | Yes — raised | `max(prev_trailing, current × 0.85)` |
| 50% gain | Yes — raised | `max(prev_trailing, current × 0.85)` |

---

## 6. Gate-Based Exit

If the environment gate deteriorates to `NO-GO` for `GATE_SUSTAINED_NOGO_SNAPS` consecutive snaps (default: 2 = 10 minutes), the tracker exits the trade.

```python
nogo_snaps = [s for s in recent_snaps if s["gate_verdict"] == GateVerdict.NOGO]

if len(nogo_snaps) >= settings.GATE_SUSTAINED_NOGO_SNAPS:
    close_trade(exit_reason=ExitReason.GATE_NOGO, ...)
```

This exits a position even if the premium has not hit the SL — protecting against holding in a structurally hostile environment (e.g., GEX flip, VCoC reversal, PCR divergence collapse).

---

## 7. Shadow Tracker (`ai/shadowtracker.py`)

### 7.1 Purpose

The shadow tracker hypothetically tracks `REJECTED` and `EXPIRED` trades to answer: *"What would have happened if that trade was taken?"*

```python
def track_shadow_positions(
    conn: duckdb.DuckDBPyConnection,
    jconn: sqlite3.Connection,
    trade_date: str,
    snap_time: str,
) -> None
```

Runs as **Step 9** of the scheduler loop, after live tracking (Step 8).

### 7.2 Shadow Snaps

Same schema as `position_snaps` but stored in `shadow_snaps`. Shadow positions are subject to the same SL, target, theta SL, gate exit, and IV crush rules — but never trigger actual API calls or notifications.

### 7.3 Finalisation (`ai/eod.py`)

At EOD (15:25 snap), `finalize_all_shadows()` assigns a `ShadowOutcome` to each shadow position:

| Outcome | Condition |
|---|---|
| `CLEAN_MISS` | Shadow hit target; trade was REJECTED/EXPIRED — missed win |
| `GOOD_SKIP` | Shadow hit SL; was correctly rejected — avoided loss |
| `RISKY_MISS` | Shadow barely hit target within marginal conditions |
| `BREAKEVEN` | Shadow closed near entry; negligible outcome |
| `PENDING` | Still open at EOD; force-closed at last LTP |

Results feed `learning/regret.py` to calibrate future pre-flight thresholds.

---

## 8. EOD Force Close (`ai/eod.py`)

At `EOD_SWEEP_TIME` (15:25), any remaining `ACCEPTED` trade is force-closed regardless of P&L. Intraday options are never held overnight.

```python
def eod_force_close(
    conn: duckdb.DuckDBPyConnection,
    jconn: sqlite3.Connection,
    trade_date: str,
) -> None:

    open_trades = get_trades_by_status(jconn, TradeStatus.ACCEPTED)
    for trade in open_trades:
        current_ltp = fetch_ltp(conn, trade["underlying"], "15:25")
        close_trade(
            jconn,
            trade["trade_id"],
            exit_premium=current_ltp,
            exit_reason=ExitReason.EOD_FORCE,
            snap_time="15:25",
        )
        log.info(f"EOD force-close: {trade['trade_id']}, ltp={current_ltp}")
```

Following force-close, `finalize_all_shadows()` runs to close all shadow positions, then `reports/daily.py` generates the EOD markdown report.

---

## 9. Market Alerts (`analytics/alerts.py`)

### 9.1 Alert Types

| Alert Type | Trigger Condition | Severity |
|---|---|---|
| `COC_VELOCITY` | VCoC crosses ±10 threshold | HIGH |
| `GEX_DECLINE` | `pct_of_peak` crosses below 70% | MEDIUM |
| `PCR_DIVERGENCE` | Divergence crosses +0.25 or −0.20 | HIGH |
| `OBI_SHIFT` | ATM OBI crosses ±0.15 | MEDIUM |
| `VOLUME_SPIKE` | Volume ratio crosses 2.0× rolling median | MEDIUM |
| `GATE_CHANGE` | Gate verdict transitions (any direction) | HIGH |

### 9.2 Transition Detection (LAG Pattern)

Alerts fire **only on state transition**, not on every snap. This prevents alert flooding during sustained signal conditions.

```sql
WITH signal_series AS (
  SELECT
    snap_time,
    pct_of_peak,
    LAG(pct_of_peak) OVER (ORDER BY snap_time) AS prev_pct
  FROM   gex_per_snap
  WHERE  trade_date = :trade_date
    AND  underlying = :underlying
)
SELECT snap_time, 'GEX_DECLINE' AS type
FROM   signal_series
WHERE  pct_of_peak <  70          -- current: below threshold
  AND  prev_pct    >= 70          -- previous: above threshold (transition)
  AND  snap_time   >= :since_snap -- only within ALERT_MAX_AGE_SNAPS window
```

The same LAG pattern applies to `COC_VELOCITY`, `PCR_DIVERGENCE`, `OBI_SHIFT`, `VOLUME_SPIKE`, and `GATE_CHANGE`.

### 9.3 Alert Response Format

```python
{
    "time":      "10:15",
    "type":      "COC_VELOCITY",
    "severity":  "HIGH",
    "direction": "BULL",           # CE / PE / None
    "headline":  "VCoC crossed 10 — now 14.3",
    "message":   "Institutional long accumulation signal active"
}
```

### 9.4 Alert Retention

Alerts are kept for the last `ALERT_MAX_AGE_SNAPS` snaps (default: 12 = 60 minutes). Older alerts are silently discarded and never shown in the UI.

---

## 10. Desktop Notifications (`notifications/alerts.py`)

Uses `plyer` for OS-level desktop toast notifications. Runs as **Step 11** of the scheduler loop.

```python
def check_and_fire(
    jconn: sqlite3.Connection,
    trade_date: str,
    snap_time: str,
) -> None
```

### 10.1 Notification Triggers

| Event | Notification |
|---|---|
| New `GENERATED` recommendation | `⚡ New Trade: NIFTY CE 23,500 — Confidence 88, Grade A` |
| Approaching target (≥ 80% of way) | `🎯 Target Zone: NIFTY CE — LTP 125.00 (target 131.25)` |
| Theta clock turns RED | `⏰ Theta Warning: NIFTY CE — Theta consuming remaining upside` |
| IV crush HIGH | `📉 IV Crush: NIFTY CE — IV down 3.2% since entry` |
| Gate turns NO-GO | `⚠️ Gate NO-GO: NIFTY — Environment deteriorated (2 snaps)` |
| Trade closed | `✅ Closed: NIFTY CE +50.4% — TARGET_HIT` |

### 10.2 Deduplication

Each notification type carries a per-trade cooldown. The same notification will not re-fire within `N` snaps to prevent noise during choppy sessions.

---

## 11. P&L Attribution (`analytics/pnl.py`)

At close, P&L is decomposed into contributing Greek components:

```python
def compute_pnl_attribution(
    entry_premium: float,
    exit_premium: float,
    delta_entry: float,
    delta_exit: float,
    theta_daily: float,
    t_held_min: int,
    iv_entry: float,
    iv_exit: float,
    vega: float,
) -> dict:

    delta_pnl = (delta_entry + delta_exit) / 2 * spot_move    # midpoint delta approx
    theta_pnl = -abs(theta_daily) * (t_held_min / 390)
    vega_pnl  = vega * (iv_exit - iv_entry) * 100
    residual  = (exit_premium - entry_premium) - delta_pnl - theta_pnl - vega_pnl

    return {
        "total_pnl":  exit_premium - entry_premium,
        "delta_pnl":  delta_pnl,    # directional move contribution
        "theta_pnl":  theta_pnl,    # time decay cost
        "vega_pnl":   vega_pnl,     # IV expansion/contraction contribution
        "residual":   residual,     # higher-order Greeks (gamma, vanna, etc.)
    }
```

P&L attribution is stored with each closed trade and surfaced in the Learning Report to answer questions like: *"Was this a good exit, or did theta drain a winning trade?"*

---

## 12. WebSocket Events (`api/ws.py`)

Live position updates are broadcast to connected frontend clients via WebSocket after each tracker run.

| Event | Payload |
|---|---|
| `position.snap` | Latest `position_snap` dict for an open trade |
| `position.closed` | Closed trade summary with exit reason and P&L |
| `alert.fired` | New market alert from `analytics/alerts.py` |
| `recommendation.new` | New `GENERATED` trade card |

The frontend subscribes at startup and updates the live position panel and alert feed in real time without polling.
