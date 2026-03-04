# OptDash — Part 7: Environment Gate

The Environment Gate is the system's first-line filter that decides whether the current market environment is acceptable for issuing (or continuing to hold) a directional option-buying trade.

It is implemented as a point-based checklist with **6 core conditions** plus **2 bonus conditions** (VEX/CEX related). The gate returns a structured dict with per-condition detail for auditability and UI rendering.

---

## 1. Function Signature

```python
def get_environment_score(
    conn: duckdb.DuckDBPyConnection,
    trade_date: str,
    snap_time:  str,
    underlying: str,
    direction:  str | None = None,   # "CE", "PE", or None
) -> dict
```

Follows the analytics layer design contract: read-only DuckDB, no writes, returns a serialisable dict.

---

## 2. Return Format

```python
{
    "score":    int,          # 0..11
    "maxscore": int,          # always 11 (settings.GATE_MAX_SCORE)
    "verdict":  "GO" | "WAIT" | "NOGO",
    "conditions": {
        "<key>": {
            "met":       bool,
            "value":     Any,
            "threshold": str,
            "points":    int,   # 0 or 1
            "note":      str,
            "is_bonus":  bool   # True for bonus conditions only
        },
        ...
    }
}
```

The `conditions` dict is the exact data structure rendered by the UI gate panel and stored in recommendation logs. It makes every gate decision fully auditable.

---

## 3. Verdict Thresholds

| Verdict | Score Range | Config Key | Effect |
|---|---|---|---|
| `GO`   | ≥ 5 | `GATE_GO_THRESHOLD` (default 5) | Environment acceptable to trade |
| `WAIT` | 3–4 | `GATE_WAIT_THRESHOLD` (default 3) | Mixed; no new trade issued |
| `NOGO` | ≤ 3 | — | Hostile; no trade, exit if open |

`max_score = 11` (`GATE_MAX_SCORE`): 9 from core, 2 from bonus.

---

## 4. The 8 Conditions

### 4.1 Core Conditions (max 6 pts — always evaluated)

**Condition 1 — `gex_declining`**

```python
gex = get_net_gex(conn, trade_date, snap_time, underlying)
met = gex["pct_of_peak"] < settings.GEX_DECLINE_THRESHOLD   # default 70
points = 1 if met else 0
note = f"GEX at {pct_of_peak:.0f}% of peak"
```

GEX has declined ≥ 30% from the day's maximum. A falling gamma wall means fewer pinning forces and better conditions for directional movement.

---

**Condition 2 — `vcoc_signal`**

```python
coc = get_coc_latest(conn, trade_date, snap_time, underlying)
met = abs(coc["vcoc_15m"] or 0) >= settings.VCOC_BULL_THRESHOLD   # default 10.0
points = 1 if met else 0
note = f"VCoC {coc['vcoc_15m']:.1f} {'BULL' if coc['vcoc_15m'] > 0 else 'BEAR'}"
```

15-minute cost-of-carry velocity has crossed ±10 — indicating institutional accumulation (bull) or unwinding (bear).

---

**Condition 3 — `futbsratio`**

```python
fut_obi = get_futures_obi(conn, trade_date, snap_time, underlying)
met = fut_obi < settings.FUTOBIBEARTHRESHOLD   # default -0.20
points = 1 if met else 0
note = f"Futures OBI {fut_obi:.3f}"
```

Futures order-book imbalance is ≤ -0.20, meaning sellers are dominant in the futures tape.

---

**Condition 4 — `pcr_divergence`**

```python
pcr = get_pcr(conn, trade_date, snap_time, underlying)
div = pcr["pcr_divergence"]   # PCR(vol) − PCR(OI)
met = (div >= settings.PCRDIVBULLTHRESHOLD    # +0.25  retail panic-puts
    or div <= settings.PCRDIVBEARTHRESHOLD)   # -0.20  retail panic-calls
points = 1 if met else 0
```

Retail vs institutional divergence — the volume-based PCR has deviated meaningfully from the OI-based PCR, signalling a retail-vs-smart-money mismatch.

| Divergence | Meaning |
|---|---|
| ≥ +0.25 | `RETAIL_PANIC_PUTS` — retail bearish, smart money opposite |
| ≤ -0.20 | `RETAIL_PANIC_CALLS` — retail euphoric, fade it |

---

**Condition 5 — `ivp_cheap`**

```python
iv_data = get_ivr_ivp(conn, trade_date, snap_time, underlying)
ivp = iv_data["ivp"] or 100
met = ivp < settings.IVPCHEAPTHRESHOLD   # default 50.0
points = 1 if met else 0
note = f"IVP {ivp:.0f}th percentile"
```

Implied volatility percentile (based on 90-day history, `IVR_IVP_LOOKBACK=90`) is below the 50th percentile — options are "cheap" relative to history, reducing adverse premium risk.

---

**Condition 6 — `obi_negative`**

```python
obi = get_atm_obi(conn, trade_date, snap_time, underlying)
met = abs(obi) >= settings.OBI_THRESHOLD   # default 0.10
points = 1 if met else 0
note = f"OBI {obi:.3f} {'call absorbing' if obi > 0 else 'put absorbing'}"
```

ATM options order-book imbalance is strong enough (absolute value ≥ 0.10) to signal directional order-flow bias.

---

### 4.2 Bonus Conditions (max 2 pts — directional / expiry)

**Bonus Condition 7 — `vex_aligned`** (`is_bonus=True`)

```python
if direction:
    vex = get_vex_cex_current(conn, trade_date, snap_time, underlying)
    vex_positive = vex["vex_total_M"] > 0
    vex_aligned  = (
        (direction == "CE" and vex_positive) or
        (direction == "PE" and not vex_positive)
    )
    points = 1 if vex_aligned else 0
    note   = f"VEX {vex['vex_total_M']:.1f}M {'BULLISH' if vex_positive else 'BEARISH'}"
else:
    points = 0
    note   = "Pass direction=CE/PE to evaluate"
```

VEX (Vanna Exposure) sign aligns with the proposed trade direction. Requires `direction` param to be set; without it, Gate 7 always scores 0.

---

**Bonus Condition 8 — `not_charm_distortion`** (`is_bonus=True`)

```python
is_dealer_oclock = is_dealer_oclock(snap_time, conn, trade_date, underlying)
not_charm_met = not is_dealer_oclock
points = 1 if not_charm_met else 0
value  = "DEALER_OCLOCK" if is_dealer_oclock else "NORMAL"
```

The system is **not** in the Dealer O'Clock window — defined as DTE ≤ 1 (`DEALER_OCLOCK_MAX_DTE`) AND time between 14:45 and 15:25 IST. During this window, CEX (Charm Exposure) dominates and can overwhelm directional signals, so the bonus point is withheld.

---

## 5. Scoring Logic

```python
# Normal scoring
score   = sum(c["points"] for c in conditions.values())
verdict = (
    GateVerdict.GO   if score >= settings.GATE_GO_THRESHOLD  else
    GateVerdict.WAIT if score >= settings.GATE_WAIT_THRESHOLD else
    GateVerdict.NOGO
)
```

The Dealer O'Clock flag is surfaced via Bonus Condition 8 — it does not zero-out the core score, but reduces the overall score by 1 (the withheld bonus point) when active.

---

## 6. Session-Based Gate Adjustments

The gate score itself is constant per snap. The **AI engine applies session overrides** to the effective minimum requirement before issuing a trade:

| Session | Adjustment |
|---|---|
| `OPENING_DRIVE` | Min gate effectively +1 (`SESSION_OPENING_GATE_BONUS`) |
| `MORNING_TREND` | No adjustment (standard) |
| `MIDDAY_CHOP` | Confidence penalty −5 (`SESSION_MIDDAY_CONFIDENCE_PENALTY`) |
| `AFTERNOON_DRIVE` | No adjustment (standard) |
| `CLOSING_CRUSH` | Min gate = 7, min confidence = 70 (`SESSION_CLOSING_MIN_GATE`) |

A score of 5 (normally `GO`) may still block a trade if session restrictions require a higher gate.

---

## 7. Gate in the Recommendation Flow

The gate is called at two points inside `generate_recommendation()`:

1. **Environment check** — gate score/verdict fetched at Step 1; `NOGO` immediately aborts the recommendation.
2. **Pre-flight check** — gate score must meet `PREFLIGHT_MIN_GATE_SCORE` (default 5) for the trade to pass pre-flight.

For DTE=1 trades, pre-flight requires a stricter gate: `PREFLIGHT_DTE1_MIN_GATE` (default 7).

---

## 8. Gate in Position Tracking

During live tracking (every 5-minute snap), the gate is re-evaluated. A sustained `NOGO` verdict triggers an automatic trade exit:

```python
nogo_snaps = count_consecutive_nogo(recent_snaps)
if nogo_snaps >= settings.GATE_SUSTAINED_NOGO_SNAPS:   # default 2
    close_trade(exit_reason=ExitReason.GATE_NOGO)
```

This protects open positions from holding through structurally deteriorating environments even when the option premium has not yet hit the SL.

---

## 9. API Endpoint

```
GET /market/environment?trade_date={date}&snap_time={time}&underlying={sym}&direction={CE|PE}
```

Returns the full `EnvironmentScore` schema (`api/schemas/responses.py`). The UI polls this at **5-second intervals** and renders:

- Large colour-coded score (green `GO`, amber `WAIT`, red `NOGO`)
- Progress bar from 0 to `maxscore` (11)
- One row per condition with live value, threshold, and points
- Contextual notes (e.g., VCoC direction, PCR signal label)

---

## 10. Checkpoint Verification

```bash
python -c "
from optdash.pipeline.duckdbgateway import startup
from optdash.analytics.environment import get_environment_score
conn = startup()
result = get_environment_score(conn, '2026-02-28', '10:15', 'NIFTY', direction='CE')
print('Gate score:', result['score'], '/', result['maxscore'])
print('Verdict:',    result['verdict'])
assert result['score'] >= 0
assert result['maxscore'] == 11
print('GATE CHECKPOINT PASSED')
"
```
