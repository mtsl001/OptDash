# OptDash — Part 4: AI Engine

All AI logic lives in `optdash/ai/`. It is entirely rule-based and template-driven — no LLM, no external model, no internet call. Every decision is explainable, auditable, and reproducible.

---

## 1. Recommendation Generation Flow

```
generate_recommendation()
        │
        ├─ [Guard] Open trade exists? → return None
        ├─ [Guard] Pending recommendation exists? → return None
        │
        ├─ [1] get_environment_score()     → gate dict (score, verdict, conditions)
        ├─ [2] get_market_session()        → MarketSession enum
        ├─ [3] get_directional_bias()      → direction, margin, signals[]
        ├─ [4] get_ivr_ivp()               → iv, ivr, ivp, shape
        ├─ [5] get_net_gex()               → net_gex, regime, spot
        ├─ [6] get_vex_cex_current()       → vex_signal, cex_signal, dealer_oclock
        ├─ [7] get_max_pain()              → max_pain_strike, distance_pct
        │
        ├─ [Guard] direction == NEUTRAL? → return None
        │
        ├─ [8] get_strikes()              → top-N strikes by S-Score
        ├─ [Guard] No candidates for direction? → return None
        ├─ [Guard] strike.ltp <= 0? → log warning, return None
        │
        ├─ [9]  stats.get_session_stats()  → win_rate, avg_pnl (historical)
        ├─ [10] compute_confidence()       → confidence 0-100, buckets
        ├─ [11] run_pre_flight()           → passed bool, failures list
        ├─ [Guard] Pre-flight failed? → log, return None
        │
        ├─ [12] Compute SL = entry * (1 - AI_SL_PCT)
        ├─ [12] Compute Target = entry * AI_TARGET_MULT
        ├─ [13] compute_quality_score()    → grade A/B/C/D, points
        ├─ [14] build_narrative()          → trade explanation text
        ├─ [15] trades.create_trade()      → trade_id (SQLite INSERT)
        │
        └─ return trade dict
```

---

## 2. Directional Bias (`ai/direction.py`)

### 2.1 Overview

The direction engine evaluates **5 independent signals**, each voting `CE` (bullish), `PE` (bearish), or abstaining. The net vote determines directional bias.

### 2.2 Signal Definitions

| Signal | CE Condition | PE Condition |
|---|---|---|
| **S1: V_CoC** | `v_coc >= VCOC_BULL_THRESHOLD` | `v_coc <= VCOC_BEAR_THRESHOLD` |
| **S2: PCR Divergence** | `pcr_div > PCR_DIV_BULL_THRESHOLD` | `pcr_div < PCR_DIV_BEAR_THRESHOLD` |
| **S3: OBI** | `obi > OBI_BULL_THRESHOLD` | `obi < OBI_BEAR_THRESHOLD` |
| **S4: VEX Signal** | `vex_signal == VEX_BULLISH` | `vex_signal == VEX_BEARISH` |
| **S5: GEX Regime** | `regime == NEGATIVE_TREND and spot_change > 0` | `regime == NEGATIVE_TREND and spot_change < 0` |

### 2.3 Voting Resolution

```python
ce_votes = count(signals == 'CE')
pe_votes = count(signals == 'PE')
margin   = abs(ce_votes - pe_votes)   # 0 to 5

if margin == 0:
    direction = Direction.NEUTRAL     # Tie — no trade issued
elif ce_votes > pe_votes:
    direction = Direction.CE
else:
    direction = Direction.PE
```

**Margin interpretation:**
- `margin=1`: Weak signal (3 vs 2 votes or 2 vs 1)
- `margin=3`: Strong signal (4 vs 1 votes)
- `margin=5`: Maximum conviction (5 vs 0)

> Ties are resolved as NEUTRAL (not a random tiebreak) — the system only trades when there is a clear majority.

---

## 3. Confidence Scoring (`ai/confidence.py`)

Confidence is a **0–100 score** composed of four independent buckets. It is capped at 100 and floored at 0. Session adjustments can modify the raw score.

### 3.1 Bucket 1 — Signal Alignment (max 40 pts)

Measures how aligned and strong the directional signals are:

```python
b1 = min(40, margin * 8 + signal_count * 2)
```

| Scenario | B1 Score |
|---|---|
| margin=1, signals=2 | min(40, 8+4) = 12 |
| margin=3, signals=4 | min(40, 24+8) = 32 |
| margin=5, signals=5 | min(40, 40+10) = 40 (capped) |

### 3.2 Bucket 2 — Gate Score (max 25 pts)

Scales the Environment Gate score linearly:

```python
b2 = min(25, int((gate_score / (settings.GATE_MAX_SCORE or 10)) * 30))
```

Example: gate_score=9 out of 11 → `int((9/11)*30)` = 24 pts.

### 3.3 Bucket 3 — Structural Quality (max 25 pts)

Six independent structural conditions, each adds points:

| Condition | Points | Guard |
|---|---|---|
| `ivp < 50` | +6 | `ivp if ivp is not None else 100` |
| IV term structure is CONTANGO | +4 | |
| `s_score > 10` (screener quality) | +7 | `s_score or 0` |
| GEX regime is NEGATIVE_TREND | +5 | |
| VEX is bullish AND direction is CE | +3 | |
| VEX is bearish AND direction is PE | +3 | |

Max without cap = 6+4+7+5+3 = 25 (perfectly aligned trade).

### 3.4 Bucket 4 — Historical Performance (max 10 pts)

```python
win_rate = learning_stats.get("win_rate", 50) / 100
b4 = min(10, int(win_rate * 12))
```

| Win Rate | B4 Score |
|---|---|
| 50% (default) | 6 |
| 70% | 8 |
| 90% | 10 (capped) |

### 3.5 Session Adjustments

```python
if session == MarketSession.MIDDAY_CHOP:
    raw -= settings.SESSION_MIDDAY_CONFIDENCE_PENALTY   # default: -10

if session == MarketSession.CLOSING_CRUSH:
    raw = min(raw, settings.SESSION_CLOSING_MIN_CONFIDENCE)  # default: cap at 70

confidence = max(0, min(100, raw))
```

### 3.6 Example Calculation

```
Trade: NIFTY CE, margin=3, signals=4, gate=9/11, ivp=30, CONTANGO,
       s_score=15, NEGATIVE_TREND, VEX_BULLISH, win_rate=65%, MIDMORNING

B1 = min(40, 3*8 + 4*2) = min(40, 32) = 32
B2 = min(25, int(9/11 * 30)) = min(25, 24) = 24
B3 = 6 + 4 + 7 + 5 + 3 = 25  (capped at 25)
B4 = min(10, int(0.65 * 12)) = min(10, 7) = 7
Raw = 32 + 24 + 25 + 7 = 88
Session adjustment: none (MIDMORNING)
Final confidence: 88
```

---

## 4. Pre-Flight Checks (`ai/pre_flight.py`)

Pre-flight is a set of **hard binary rules**. Any failure blocks the recommendation entirely regardless of confidence.

### 4.1 Rules

| Rule | Condition to PASS | Failure Message |
|---|---|---|
| PF1: Min Gate Score | `gate_score >= PRE_FLIGHT_MIN_GATE` (default 5) | `GATE_BELOW_MIN` |
| PF2: Min Confidence | `confidence >= PRE_FLIGHT_MIN_CONFIDENCE` (default 55) | `CONFIDENCE_BELOW_MIN` |
| PF3: Max Spread | `bid_ask_spread_pct <= PRE_FLIGHT_MAX_SPREAD_PCT` (default 5%) | `SPREAD_TOO_WIDE` |
| PF4: Max Pain Distance | `max_pain_distance_pct <= PRE_FLIGHT_MAX_PAIN_DIST_PCT` (default 3%) | `FAR_FROM_MAX_PAIN` |
| PF5: Session Restriction | Not `MIDDAY_CHOP` (unless overridden) | `MIDDAY_CHOP_BLOCKED` |
| PF6: No Existing Open Trades | `existing_open_trades == 0` | `POSITION_ALREADY_OPEN` |
| PF7: Dealer O’Clock | `not dealer_oclock` (unless allowed) | `DEALER_OCLOCK_ACTIVE` |

### 4.2 Spread Calculation

```python
bid_ask_spread_pct = (ask - bid) / ((ask + bid) / 2) * 100
```

Where `bid` and `ask` are the strike's ATM bid/ask prices from the screener.

### 4.3 Return Format

```python
passed, failures = run_pre_flight(...)
# passed: bool
# failures: list of failure code strings, e.g. ["CONFIDENCE_BELOW_MIN", "FAR_FROM_MAX_PAIN"]
```

Failure codes are stored in the log for diagnostics.

---

## 5. Quality Score (`ai/quality.py`)

The quality score assigns a **letter grade (A/B/C/D)** to every trade at the moment of recommendation.

### 5.1 Inputs

- Strike S-Score (from screener)
- Environment Gate score
- Confidence score

### 5.2 Composite

```python
raw = (
    W_SSCORE     * normalize(s_score, 0, 20) +
    W_GATE       * normalize(gate_score, 0, GATE_MAX_SCORE) +
    W_CONFIDENCE * normalize(confidence, 0, 100)
) * 100

# Guard against zero-denominator in normalize()
```

### 5.3 Grade Thresholds

| Grade | Raw Score Range | Meaning |
|---|---|---|
| A | ≥ 80 | Exceptional — all conditions aligned |
| B | ≥ 60 | Good — majority conditions favourable |
| C | ≥ 40 | Marginal — acceptable but weak |
| D | < 40 | Poor — issued only if pre-flight somehow passed |

---

## 6. Trade Narrative (`ai/narrative.py`)

### 6.1 Design Philosophy

Narratives are fully template-based. Each data point maps to a human-readable sentence fragment. There is no LLM or text generation model. This ensures:
- Deterministic output for the same inputs
- Auditable reasoning chain
- Zero external dependency
- Instant generation (<1ms)

### 6.2 Narrative Structure

```python
build_narrative(
    direction, gate_score, gate_verdict,
    direction_signals, iv_data, gex_data,
    vex_data, session, dealer_oclock
) -> str
```

**Output example:**
```
NIFTY CE | Gate: 9/11 (GO) | Session: MIDMORNING

Directional signals: CoC velocity bullish (futures accumulation), OBI positive (bid pressure), VEX bullish (IV-driven dealer buying).

Structural context: IV at 30th percentile (cheap premium). GEX regime is NEGATIVE_TREND — directional moves are amplified. Term structure in contango (normal).

Risk: Standard SL 15% below entry. No dealer o’clock conditions.
```

### 6.3 None Safety

All narrative components use `or` fallback patterns:
```python
iv_line = f"IV at {ivp:.0f}th percentile" if ivp is not None else "IV data unavailable"
```

Any missing data point renders a neutral placeholder — the narrative never raises an exception.

---

## 7. SL and Target Calculation

```python
entry_premium = strike["ltp"]      # Validated > 0 before this point
sl     = round(entry_premium * (1 - settings.AI_SL_PCT), 2)
target = round(entry_premium * settings.AI_TARGET_MULT, 2)
```

| Setting | Default | Example at entry=100 |
|---|---|---|
| `AI_SL_PCT` | 0.15 (15%) | SL = 85 |
| `AI_TARGET_MULT` | 1.30 (30% gain) | Target = 130 |

These are **initial values**. The live tracker modifies them dynamically via theta-adjusted SL and trailing stop.

---

## 8. Summary: What Blocks a Recommendation?

A recommendation fails to generate if **any** of the following are true:

| Block Point | Condition |
|---|---|
| Open trade guard | ACCEPTED trade exists for this underlying |
| Pending guard | GENERATED (unactioned) trade exists |
| Neutral direction | Direction signal tie |
| No candidates | No TIER1 strikes match the direction |
| Zero LTP | Best strike has ltp = 0 |
| Pre-flight failure | Any of 7 hard rules fails |

Only after passing all gates does the trade card get written to the journal.
