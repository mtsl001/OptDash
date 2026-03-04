# OptDash — Part 8: Learning Engine

The learning engine is a deterministic, journal-driven feedback layer. It aggregates closed-trade outcomes and shadow-trade counterfactuals to improve scoring accuracy and guide threshold calibration over time. All logic lives in `optdash/learning/`.

---

## 1. Design Contract

1. The SQLite journal (`journal.db`) is the sole data source — no DuckDB reads.
2. Learning functions are **read-then-aggregate**: they query the journal and return computed summaries; they never write to DuckDB or Parquet files.
3. Learning outputs are consumed by the AI engine (confidence scoring Bucket 4) and by the EOD report.
4. No module in `learning/` imports from `ai/` or `analytics/` — it only imports from `journal/`.

---

## 2. Data Sources

| Source | Content | Used by |
|---|---|---|
| `trades` table | All `CLOSED` trade outcomes — status, exit_reason, pnl_pts, pnl_pct, confidence, signals, gate_score | stats, signals, calibration, direction |
| `position_snaps` table | Per-snap telemetry of accepted trades | timeanalysis |
| `shadow_snaps` + `shadow_outcome` | Counterfactual tracking for REJECTED/EXPIRED | regret |

---

## 3. Module Map

```
optdash/learning/
    __init__.py
    stats.py          ← aggregate win rate, P&L, streaks
    signals.py        ← per-signal accuracy + decay detection
    calibration.py    ← confidence calibration bands
    direction.py      ← CE vs PE accuracy split
    regret.py         ← rejected/expired outcome analysis
    timeanalysis.py   ← time-of-day performance heatmap
    suggestions.py    ← gate threshold adjustment suggestions
```

---

## 4. `learning/stats.py` — Aggregate Performance

### 4.1 Function

```python
def get_session_stats(
    conn: sqlite3.Connection,
    underlying:    str | None = None,
    session:       str | None = None,
    lookback_days: int = 30,
) -> dict
```

### 4.2 Outputs

```python
{
    "trade_count":  int,
    "win_count":    int,
    "loss_count":   int,
    "win_rate":     float,   # 0.0..1.0  (e.g. 0.65 = 65%)
    "avg_pnl_pts":  float,
    "avg_pnl_pct":  float,
    "max_win_pct":  float,
    "max_loss_pct": float,
    "win_streak":   int,     # current consecutive wins
    "loss_streak":  int,     # current consecutive losses
    "expectancy":   float,   # win_rate * avg_win - (1 - win_rate) * avg_loss
}
```

### 4.3 Win Definition

A trade is counted as a **win** if `pnl_pts > 0` at close.

### 4.4 Feedback into AI Engine

`win_rate` from `get_session_stats()` feeds directly into **Bucket 4** of `compute_confidence()` (see Part 4, Section 3.4):

```python
b4 = min(10, int(win_rate * 12))
```

At default win rate (50%) → B4 = 6. At 70% → B4 = 8. At ≥ 83% → B4 = 10 (capped).

---

## 5. `learning/signals.py` — Per-Signal Accuracy

### 5.1 Purpose

Tracks how each directional signal performs historically and detects when a previously reliable signal is decaying.

### 5.2 Signal Accuracy Record (per signal, per direction)

```python
{
    "signal_key":  str,    # e.g. "S1_VCOC", "S2_PCR_DIV"
    "direction":   str,    # "CE" or "PE"
    "fired_count": int,    # trades where this signal was aligned
    "win_count":   int,
    "accuracy":    float,  # win_count / fired_count
    "decay_flag":  bool,   # True if accuracy < historical baseline by threshold
}
```

### 5.3 Decay Detection

A signal is flagged as decaying when its recent-window accuracy drops below its historical baseline by a configurable margin. Decay flags are surfaced in `suggestions.py` as recommendations to re-evaluate the signal's threshold.

---

## 6. `learning/calibration.py` — Confidence Calibration

### 6.1 Purpose

Answers: *"When the system reports confidence = 80, what is the actual historical hit-rate for trades at that confidence level?"*

### 6.2 Calibration Bands

Closed trades are grouped into confidence buckets and actual win-rates are computed per bucket:

| Confidence Band | Realized Hit Rate |
|---|---|
| 50–59 | computed |
| 60–69 | computed |
| 70–79 | computed |
| 80–89 | computed |
| 90–100 | computed |

### 6.3 Output

```python
def get_calibration_bands(
    conn: sqlite3.Connection,
) -> list[dict]:
    # [{"band_low", "band_high", "trade_count", "realized_win_rate"}, ...]
```

Calibration data is included in the Learning Report and used to assess whether the confidence score is well-calibrated or needs rescaling.

---

## 7. `learning/direction.py` — CE vs PE Accuracy

### 7.1 Purpose

Aggregates trade performance split by trade direction (`CE` vs `PE`) to detect directional asymmetry.

### 7.2 Output

```python
def get_directional_accuracy(
    conn: sqlite3.Connection,
    lookback_days: int = 30,
) -> dict:
    return {
        "CE": {"trade_count": int, "win_rate": float, "avg_pnl_pct": float},
        "PE": {"trade_count": int, "win_rate": float, "avg_pnl_pct": float},
    }
```

If one direction shows a consistently lower win-rate, `suggestions.py` will recommend raising the pre-flight minimum confidence or gate threshold for that direction.

---

## 8. `learning/regret.py` — Rejected/Expired Outcome Analysis

### 8.1 Purpose

Consumes shadow tracking outcomes to evaluate the cost of rejecting or expiring trades. Answers: *"How much opportunity was missed by being conservative?"*

### 8.2 Shadow Outcomes Used

| `ShadowOutcome` | Meaning |
|---|---|
| `CLEAN_MISS` | Shadow hit target — a rejected/expired trade would have won clearly |
| `GOOD_SKIP` | Shadow hit SL — correctly rejected, avoided a loss |
| `RISKY_MISS` | Shadow barely hit target — win but marginal |
| `BREAKEVEN` | Shadow closed near entry — negligible outcome |
| `PENDING` | Not yet finalised (intraday only) |

### 8.3 Regret Metrics

```python
def get_regret_summary(
    conn: sqlite3.Connection,
    lookback_days: int = 30,
) -> dict:
    return {
        "total_shadows":    int,
        "clean_miss_count": int,
        "good_skip_count":  int,
        "risky_miss_count": int,
        "breakeven_count":  int,
        "regret_rate":      float,  # (clean_miss + risky_miss) / total_shadows
        "precision_rate":   float,  # good_skip / total_shadows
        "avg_missed_pnl":   float,  # avg pnl of CLEAN_MISS shadows
    }
```

`regret_rate` measures how often the system was too conservative. `precision_rate` measures how often the rejection was correct.

---

## 9. `learning/timeanalysis.py` — Time-of-Day Performance

### 9.1 Purpose

Breaks down performance by session to identify when the system performs best and worst.

### 9.2 Output

```python
def get_time_performance(
    conn: sqlite3.Connection,
    lookback_days: int = 30,
) -> list[dict]:
    # per-session rows:
    # [{"session", "trade_count", "win_rate", "avg_pnl_pct", "avg_confidence"}, ...]
```

Sessions: `OPENING_DRIVE`, `MORNING_TREND`, `MIDDAY_CHOP`, `AFTERNOON_DRIVE`, `CLOSING_CRUSH`.

This data feeds the Learning page heatmap in the frontend and informs session-based gate adjustments.

---

## 10. `learning/suggestions.py` — Threshold Suggestions

### 10.1 Purpose

Produces deterministic, human-readable suggestions for threshold adjustments based on aggregated learning data. Does **not** auto-modify config — it only generates recommendations.

### 10.2 Output

```python
def get_suggestions(
    conn: sqlite3.Connection,
) -> list[dict]:
    return [
        {
            "type":      str,   # "GATE_THRESHOLD", "CONFIDENCE_FLOOR", "SIGNAL_REVIEW"
            "parameter": str,   # e.g. "GATE_GO_THRESHOLD", "AI_MIN_CONFIDENCE"
            "current":   Any,   # current config value
            "suggested": Any,   # suggested new value
            "reason":    str,   # human-readable justification
            "priority":  str,   # "HIGH" | "MEDIUM" | "LOW"
        },
        ...
    ]
```

### 10.3 Suggestion Sources

| Trigger | Suggestion Type |
|---|---|
| `win_rate < 45%` sustained | Raise `GATE_GO_THRESHOLD` or `AI_MIN_CONFIDENCE` |
| `regret_rate > 60%` | Lower `PREFLIGHT_MIN_GATE_SCORE` (too conservative) |
| Signal decay flag active | Review signal threshold for that condition |
| `MIDDAY_CHOP` win_rate much lower than other sessions | Increase midday confidence penalty |
| `CLOSING_CRUSH` clean misses high | Relax `SESSION_CLOSING_MIN_GATE` |

---

## 11. Update Triggers and Sequence

```
EOD Sweep (15:25 snap)
    │
    ├─ eod.eod_force_close()         → closes all remaining ACCEPTED trades
    ├─ eod.finalize_all_shadows()    → assigns ShadowOutcome to all shadows
    │
    ├─ learning/stats.py             → update win_rate, avg_pnl, streaks
    ├─ learning/signals.py           → update per-signal accuracy
    ├─ learning/calibration.py       → update confidence calibration bands
    ├─ learning/direction.py         → update CE/PE accuracy
    ├─ learning/regret.py            → update regret metrics from shadows
    ├─ learning/timeanalysis.py      → update session heatmap
    └─ learning/suggestions.py      → generate updated suggestions

reports/daily.py                     → reads all learning outputs for EOD report
```

Learning updates run **after** EOD finalisation and **before** the daily report is generated.

---

## 12. Learning Report API

```
GET /ai/learning?underlying={sym}&lookback_days={n}
```

Returns the `LearningReport` schema (`api/schemas/responses.py`) which bundles:

- `stats` (from `stats.py`)
- `calibration_bands` (from `calibration.py`)
- `directional_accuracy` (from `direction.py`)
- `regret_summary` (from `regret.py`)
- `time_performance` (from `timeanalysis.py`)
- `suggestions` (from `suggestions.py`)

The Learning page in the frontend (`pages/Learning.tsx`) renders all of these into a single dashboard view.

---

## 13. Sprint 5 — Files to Create

```
17. optdash/learning/__init__.py
18. optdash/learning/stats.py
19. optdash/learning/signals.py
20. optdash/learning/calibration.py
21. optdash/learning/direction.py
22. optdash/learning/regret.py
23. optdash/learning/timeanalysis.py
24. optdash/learning/suggestions.py
```

### Checkpoint

```bash
python -c "
from optdash.learning.stats import get_session_stats
from optdash.journal.schema import init_db
import sqlite3, tempfile, os

tmp = tempfile.mktemp(suffix='.db')
conn = sqlite3.connect(tmp)
init_db(conn)
result = get_session_stats(conn)
assert result['win_rate'] >= 0
assert result['trade_count'] == 0   # empty DB baseline
print('LEARNING CHECKPOINT PASSED')
os.unlink(tmp)
"
```
