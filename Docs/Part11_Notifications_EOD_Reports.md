# OptDash — Part 11: Notifications & EOD Reports

This part covers the three end-of-pipeline modules: desktop notifications (`notifications/alerts.py`), EOD position management (`ai/eod.py`), and the daily markdown report generator (`reports/daily.py`).

---

## 1. `notifications/alerts.py`

### 1.1 Purpose

Delivers OS-level desktop notifications via `plyer` for high-priority trade events. Called at **Step 11** of the scheduler's 5-minute tick loop.

```python
# scheduler.py Step 11
with journal_conn() as jconn:
    notifications.check_and_fire(jconn, trade_date, snap_time)
```

### 1.2 Primary Function

```python
def check_and_fire(
    jconn:      sqlite3.Connection,
    trade_date: str,
    snap_time:  str,
) -> None:
```

This function runs once per 5-minute tick. It checks for new events in the journal and fires a desktop notification for each one that has not already been notified. A simple `notified_ids` set (stored in-process) prevents duplicate firing.

### 1.3 Notification Events

| Event | Trigger | Title | Severity |
|---|---|---|---|
| `NEW_RECOMMENDATION` | New `GENERATED` trade inserted this snap | `OptDash — Trade Ready` | HIGH |
| `TARGET_HIT` | Trade closed with `exit_reason=TARGET_HIT` | `✅ Target Hit` | HIGH |
| `SL_HIT` | Trade closed with `exit_reason=SL_HIT` or `THETA_SL_HIT` | `❌ Stop Loss Hit` | HIGH |
| `GATE_NOGO_EXIT` | Trade closed with `exit_reason=GATE_NOGO` | `⚠️ Gate NOGO — Exited` | HIGH |
| `IV_CRUSH_EXIT` | Trade closed with `exit_reason=IV_CRUSH` | `📉 IV Crush Exit` | MEDIUM |
| `EOD_FORCE_CLOSE` | Trade closed with `exit_reason=EOD_FORCE` | `🔔 EOD Force Close` | MEDIUM |
| `HIGH_ALERT` | New `AlertSeverity.HIGH` market alert | `🚨 [AlertType]` | HIGH |

### 1.4 Plyer Call Pattern

```python
from plyer import notification as plyer_notify

def _fire(
    title:   str,
    message: str,
    timeout: int = 8,
) -> None:
    try:
        plyer_notify.notify(
            title=title,
            message=message,
            app_name="OptDash",
            timeout=timeout,
        )
    except Exception:
        pass   # plyer may fail silently on headless/test environments
```

All plyer calls are wrapped in `try/except` so a notification failure never propagates to the scheduler loop.

### 1.5 Deduplication

```python
_fired_ids: set[str] = set()   # module-level; persists for process lifetime

def _already_fired(event_key: str) -> bool:
    if event_key in _fired_ids:
        return True
    _fired_ids.add(event_key)
    return False
```

Event keys are composed as `f"{event_type}:{trade_id}"` (e.g. `"TARGET_HIT:42"`) so each event fires exactly once per process run.

---

## 2. `ai/eod.py` — EOD Force-Close & Shadow Finalization

### 2.1 Purpose

Two functions called at **Step 12** of the scheduler, only on the `15:25` snap:

```python
# scheduler.py Step 12 — only at 15:25
if snap_time == settings.EOD_SWEEP_TIME:       # "15:25"
    with journal_conn() as jconn:
        eod.eod_force_close(conn, jconn, trade_date)
        eod.finalize_all_shadows(conn, jconn, trade_date)
        reports.daily.generate(conn, jconn, trade_date)
```

### 2.2 `eod_force_close(conn, jconn, trade_date)`

```python
def eod_force_close(
    conn:       duckdb.DuckDBPyConnection,
    jconn:      sqlite3.Connection,
    trade_date: str,
) -> list[int]:
    """
    Close all ACCEPTED trades at 15:20 with exit_reason=EOD_FORCE.
    Returns list of closed trade_ids.
    """
```

**Logic:**
1. Query `get_open_trades(jconn, trade_date)` — all `status=ACCEPTED` trades for today.
2. For each open trade, fetch the latest available LTP from DuckDB for `(trade_date, "15:20", underlying, strike, option_type)`.
3. Compute `pnl_pts = exit_premium - entry_premium`, `pnl_pct = pnl_pts / entry_premium * 100`.
4. Call `trades.close_trade(jconn, trade_id, exit_premium, exit_spot, "15:20", ExitReason.EOD_FORCE, pnl_pts, pnl_pct)`.
5. Broadcast `TRADE_CLOSED` WebSocket event.
6. Log each closure at `INFO` level.

The force-close time is `settings.EOD_FORCE_CLOSE_TIME` (default `"15:20"`). The sweep runs at `15:25` but uses the `15:20` LTP snap for actual exit pricing, as the `15:25` snap may be post-close.

### 2.3 `finalize_all_shadows(conn, jconn, trade_date)`

```python
def finalize_all_shadows(
    conn:       duckdb.DuckDBPyConnection,
    jconn:      sqlite3.Connection,
    trade_date: str,
) -> None:
    """
    Assign ShadowOutcome to all REJECTED/EXPIRED trades from today
    whose shadow_outcome is still PENDING.
    """
```

**Logic per shadow trade:**

```python
from optdash.journal.shadow import get_shadow_snaps
from optdash.journal.trades import update_shadow_outcome
from optdash.states import ShadowOutcome

for trade in get_shadow_trades(jconn, trade_date):
    snaps = get_shadow_snaps(jconn, trade["id"])
    if not snaps:
        continue

    any_target_hit = any(s["hit_target"] for s in snaps)
    any_sl_hit     = any(s["hit_sl"]     for s in snaps)
    final_pnl_pct  = snaps[-1]["pnl_pct"] or 0.0

    if any_target_hit:
        outcome = ShadowOutcome.CLEAN_MISS
    elif any_sl_hit and not any_target_hit:
        outcome = ShadowOutcome.GOOD_SKIP
    elif final_pnl_pct > 2.0:
        outcome = ShadowOutcome.RISKY_MISS
    elif -2.0 <= final_pnl_pct <= 2.0:
        outcome = ShadowOutcome.BREAKEVEN
    else:
        outcome = ShadowOutcome.GOOD_SKIP   # negative, no SL flag

    update_shadow_outcome(jconn, trade["id"], outcome)
```

### 2.4 ShadowOutcome Decision Matrix

| Condition | `shadow_outcome` | Meaning |
|---|---|---|
| Any snap `hit_target=1` | `CLEAN_MISS` | Rejected/expired trade would have won |
| `hit_sl=1`, no target | `GOOD_SKIP` | Correctly avoided a loss |
| Final `pnl_pct > 2%`, no flag | `RISKY_MISS` | Would have won but marginal |
| `−2% ≤ pnl_pct ≤ +2%` | `BREAKEVEN` | Negligible outcome |
| Final `pnl_pct < −2%`, no flag | `GOOD_SKIP` | Would have lost regardless |

---

## 3. `reports/daily.py` — EOD Markdown Report

### 3.1 Purpose

Generates a formatted markdown file at `data/reports/{trade_date}.md` summarising the full day's trading activity, market environment, and learning metrics.

```python
def generate(
    conn:       duckdb.DuckDBPyConnection,
    jconn:      sqlite3.Connection,
    trade_date: str,
) -> Path:
    """
    Generates and writes the EOD markdown report.
    Returns the path to the written file.
    """
```

### 3.2 Report Structure

```markdown
# OptDash EOD Report — {trade_date}
Generated: {datetime} IST

## 1. Session Summary
- Market sessions: OPENING_DRIVE | MORNING_TREND | ...
- Gate verdicts by snap (GO/WAIT/NOGO counts)

## 2. Trade Log
| # | Underlying | Dir | Strike | Expiry | Entry | Exit | P&L pts | P&L % | Status | Exit Reason |

## 3. P&L Summary
- Total trades: N
- Win / Loss: W / L  (win rate: X%)
- Total P&L pts: +/- N
- Average P&L %: N%
- Best trade: +N pts (underlying, direction)
- Worst trade: -N pts (underlying, direction)

## 4. Rejected / Expired Outcome (Shadow Analysis)
| Trade | Strike | Direction | Shadow Outcome | Missed P&L |
- Regret rate: X%   (CLEAN_MISS + RISKY_MISS / total shadows)
- Precision rate: X%  (GOOD_SKIP / total shadows)

## 5. Learning Metrics (30-day rolling)
- Win rate:      X%
- Expectancy:    +N pts
- Confidence calibration: [table per band]
- CE accuracy:  X%  |  PE accuracy: X%

## 6. Suggestions
[From learning/suggestions.py — one bullet per HIGH/MEDIUM suggestion]

## 7. Environment Snapshot (15:25 gate)
- Gate score: N / 11  (VERDICT)
- Per-condition table
```

### 3.3 File Path

```python
report_path = settings.REPORTS_DIR / f"{trade_date}.md"
report_path.write_text(content, encoding="utf-8")
```

`settings.REPORTS_DIR` resolves to `data/reports/`.

### 3.4 Data Sources

| Section | Source |
|---|---|
| Session summary | `analytics.regime.get_market_session()` + gate snaps |
| Trade log | `journal.trades.get_closed_trades()` |
| P&L summary | Computed from closed trades |
| Shadow analysis | `journal.trades.get_shadow_trades()` + `learning.regret` |
| Learning metrics | All `learning/` modules |
| Suggestions | `learning.suggestions.get_suggestions()` |
| Environment snapshot | `analytics.environment.get_environment_score()` at 15:25 |

### 3.5 Error Handling

```python
try:
    generate(conn, jconn, trade_date)
except Exception:
    log.exception("EOD report generation failed — non-fatal")
```

Report generation failure is non-fatal. The trade journal and learning data are already written; the report is a derived artifact.

---

## 4. EOD Sequence in Full

```
15:25 snap (CLOSING_CRUSH session)
    │
    ├─ Step 7:  expire_stale_recommendations()     ← any lingering GENERATED
    ├─ Step 8:  track_open_positions()             ← last live snap
    ├─ Step 9:  track_shadow_positions()           ← last shadow snap
    ├─ Step 10: generate_recommendation()          ← no-op (gate blocks in CLOSING_CRUSH)
    ├─ Step 11: check_and_fire()                   ← notifications
    │
    └─ Step 12: EOD Sweep
            ├─ eod_force_close()                  ← closes all ACCEPTED → CLOSED/EOD_FORCE
            ├─ finalize_all_shadows()             ← assigns ShadowOutcome to all PENDING
            └─ reports.daily.generate()           ← writes data/reports/{date}.md
```

Learning module reads (stats, signals, calibration, direction, regret, timeanalysis, suggestions) happen **inside** `reports.daily.generate()` using the now-complete journal data.

---

## 5. Sprint 6 — Files to Create

```
44. optdash/notifications/__init__.py
45. optdash/notifications/alerts.py
46. optdash/ai/eod.py
47. optdash/reports/__init__.py
48. optdash/reports/daily.py
```

### Checkpoint

```bash
python -c "
import sqlite3, tempfile, os, duckdb
from optdash.journal.schema import init_db
from optdash.journal.trades import insert_trade
from optdash.ai.eod import eod_force_close, finalize_all_shadows
import json

tmp = tempfile.mktemp(suffix='.db')
jconn = sqlite3.connect(tmp)
jconn.row_factory = sqlite3.Row
init_db(jconn)
conn = duckdb.connect()   # empty, no open trades to close

# Should complete without error on empty journal
eod_force_close(conn, jconn, '2026-02-28')
finalize_all_shadows(conn, jconn, '2026-02-28')
print('EOD CHECKPOINT PASSED')
jconn.close()
conn.close()
os.unlink(tmp)
"
```
