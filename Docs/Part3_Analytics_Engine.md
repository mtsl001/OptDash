# OptDash — Part 3: Analytics Engine

All analytics modules live in `optdash/analytics/`. They are pure functions — no state, no side effects — that take a DuckDB connection plus time/underlying coordinates and return dicts or lists.

---

## 1. GEX — Gamma Exposure (`analytics/gex.py`)

### 1.1 What is GEX?

Gamma Exposure (GEX) measures the aggregate dollar gamma that **market makers (dealers)** hold across all strikes. Because dealers are typically short options (they sell them to retail), their net position is short gamma. To hedge their delta exposure, they must **buy when price rises** and **sell when price falls** (buying high, selling low) — this is the classic gamma pin / suppression effect.

- **Positive GEX**: Dealers are net long gamma → they buy dips and sell rips → suppresses volatility (range-bound)
- **Negative GEX**: Dealers are net short gamma → they sell dips and buy rips → amplifies moves (trending)

### 1.2 `get_net_gex(conn, trade_date, snap_time, underlying) → dict`

Computes the current net GEX snapshot.

```sql
SELECT
    arg_max(spot, snap_time)   AS spot,          -- Latest snap's spot price
    arg_max(futures_price, snap_time) AS futures_price,
    SUM(CASE WHEN option_type='CE' THEN gamma * oi * spot * spot * 0.01
             ELSE -gamma * oi * spot * spot * 0.01 END) AS net_gex
FROM options_data
WHERE trade_date=? AND snap_time=? AND underlying=? AND expiry_tier='TIER1'
```

**GEX formula per strike:**
```
GEX_CE = +gamma × OI × spot² × 0.01
GEX_PE = -gamma × OI × spot² × 0.01
Net GEX = Σ(GEX_CE + GEX_PE) across all TIER1 strikes
```

> The `0.01` factor converts gamma (per 1% spot move) to absolute dollar terms.

**Returns:**
```json
{
  "snap_time": "10:30",
  "spot": 22150.5,
  "futures_price": 22162.0,
  "net_gex": 4520000000,
  "pct_of_peak": 78.3,
  "regime": "POSITIVE_CHOP",
  "day_open": 22050.0,
  "day_high": 22200.0,
  "day_low": 22010.0,
  "change_pct": 0.45
}
```

### 1.3 GEX Regime Classification

| Condition | Regime | Meaning |
|---|---|---|
| `net_gex > 0` | `POSITIVE_CHOP` | Gamma pin active, range-bound expected |
| `net_gex <= 0` | `NEGATIVE_TREND` | No gamma support, directional move easier |

### 1.4 `pct_of_peak` Calculation

```sql
-- Intraday GEX peak (maximum absolute GEX seen today)
SELECT MAX(ABS(net_gex)) AS peak_gex
FROM (
    SELECT SUM(...) AS net_gex
    FROM options_data WHERE trade_date=? AND underlying=?
    GROUP BY snap_time
)
```

`pct_of_peak = (current_net_gex / peak_gex) * 100`

A declining `pct_of_peak` (e.g. dropping below 70%) indicates unwinding of gamma support — key alert trigger.

### 1.5 `get_spot_summary(conn, trade_date, underlying) → dict`

Computes correct full-day OHLC using aggregate functions:

```sql
SELECT
    arg_max(spot, snap_time) AS spot,       -- Latest snap's spot
    arg_min(spot, snap_time) AS day_open,   -- FIRST snap's spot
    MAX(spot)                AS day_high,   -- True intraday high
    MIN(spot)                AS day_low,    -- True intraday low
    arg_max(snap_time, snap_time) AS snap_time
FROM options_data
WHERE trade_date=? AND underlying=?
```

> `arg_min(spot, snap_time)` returns the spot at the earliest snap — correct day open.

### 1.6 `get_max_pain(conn, trade_date, snap_time, underlying, expiry_date) → dict`

Max Pain is the strike price at which total option writer loss is minimised. Computed by finding the strike where total payout (sum of intrinsic values weighted by OI) is minimum:

```sql
-- For each strike K, compute total payout:
-- CE payout = SUM(MAX(0, K - strike) * CE_OI) for all strikes below K
-- PE payout = SUM(MAX(0, strike - K) * PE_OI) for all strikes above K
-- Total payout = CE_payout + PE_payout
-- Max Pain = K with minimum total payout
```

---

## 2. Cost-of-Carry — CoC (`analytics/coc.py`)

### 2.1 What is Cost-of-Carry?

Cost-of-Carry (CoC) = `futures_price - spot_price`. In a rational market this should equal `spot * r * (DTE/365)`. Deviations reveal **institutional positioning**:

- **Rising CoC** (positive V_CoC): Futures bid up vs spot → institutional long accumulation
- **Falling CoC** (negative V_CoC): Futures sold vs spot → institutional unwinding/hedging

### 2.2 `get_coc_latest(conn, trade_date, snap_time, underlying) → dict`

```sql
SELECT
    AVG(spot) AS spot,
    AVG(futures_price) AS futures_price,
    AVG(futures_price - spot) AS coc,
    AVG(futures_price - spot) / NULLIF(AVG(spot), 0) * 100 AS coc_pct
FROM options_data
WHERE trade_date=? AND snap_time=? AND underlying=? AND expiry_tier='TIER1'
```

### 2.3 V_CoC — CoC Velocity (15-minute)

V_CoC measures the **rate of change** of CoC over the last 15 minutes (3 snaps):

```python
# V_CoC = CoC(now) - CoC(15 min ago)
v_coc_15m = coc_now - coc_15m_ago
```

| V_CoC | Signal | Interpretation |
|---|---|---|
| `> VCOC_BULL_THRESHOLD` | `VELOCITY_BULL` | Institutional long accumulation |
| `< VCOC_BEAR_THRESHOLD` | `VELOCITY_BEAR` | Institutional unwinding |
| Between thresholds | `STABLE` | No directional conviction |

### 2.4 ATM Order Book Imbalance (OBI)

Measures bid/ask pressure at ATM strikes:

```sql
SELECT
    (SUM(bid_qty) - SUM(ask_qty)) / NULLIF(SUM(bid_qty + ask_qty), 0) AS obi
FROM options_data
WHERE trade_date=? AND snap_time=? AND underlying=?
  AND ABS(strike_price - spot) / spot < 0.005   -- Within 0.5% of spot
  AND expiry_tier='TIER1'
```

**OBI range:** `-1.0` (pure ask — selling pressure) to `+1.0` (pure bid — buying pressure)

### 2.5 Futures OBI

Same calculation but on the futures book (not options). Measures directional institutional intent through the futures market rather than options:

```sql
-- Uses futures-specific bid/ask columns if available
-- Falls back to NULLIF-safe aggregate
```

---

## 3. Put-Call Ratio — PCR (`analytics/pcr.py`)

### 3.1 PCR Vol and PCR OI

```sql
SELECT
    SUM(CASE WHEN option_type='PE' THEN volume ELSE 0 END) /
    NULLIF(SUM(CASE WHEN option_type='CE' THEN volume ELSE 0 END), 0) AS pcr_vol,
    SUM(CASE WHEN option_type='PE' THEN oi ELSE 0 END) /
    NULLIF(SUM(CASE WHEN option_type='CE' THEN oi ELSE 0 END), 0)     AS pcr_oi
FROM options_data
WHERE trade_date=? AND snap_time=? AND underlying=? AND expiry_tier='TIER1'
```

`NULLIF(..., 0)` prevents division-by-zero when CE volume/OI is zero.

### 3.2 PCR Divergence

```python
pcr_divergence = pcr_vol - pcr_oi
```

| Divergence | Signal | Meaning |
|---|---|---|
| `> PCR_DIV_BULL_THRESHOLD` | `RETAIL_PANIC_PUTS` | Retail buying puts in panic; smart money often fades this as bullish |
| `< PCR_DIV_BEAR_THRESHOLD` | `RETAIL_PANIC_CALLS` | Retail buying calls in euphoria; smart money fades as bearish |
| `abs > 0.10` | `DIVERGENCE_BUILDING` | Divergence widening but not at extreme |
| Within range | `BALANCED` | No retail extreme |

### 3.3 Smoothed OBI (Full-Day Series)

The full-day PCR series computes a 3-snap (15-min) rolling average OBI using a SQL window function:

```sql
SELECT snap_time, pcr_vol, pcr_oi,
    AVG(obi) OVER (
        ORDER BY snap_time
        ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
    ) AS smoothed_obi
FROM (
    SELECT snap_time, ...,
        (SUM(bid_qty) - SUM(ask_qty)) / NULLIF(SUM(bid_qty + ask_qty), 0) AS obi
    FROM options_data WHERE ... GROUP BY snap_time
) sub
```

---

## 4. IV Analytics (`analytics/iv.py`)

### 4.1 IV Rank (IVR)

Measures where current IV sits relative to its 52-week high-low range:

```
IVR = (IV_current - IV_52w_low) / (IV_52w_high - IV_52w_low) × 100
```

- **IVR = 0**: Current IV at 52-week low (cheapest)
- **IVR = 100**: Current IV at 52-week high (most expensive)

### 4.2 IV Percentile (IVP)

Measures the percentage of past days where IV was **lower** than today:

```sql
WITH daily_iv AS (
    SELECT trade_date, AVG(iv) AS avg_iv
    FROM options_data
    WHERE underlying=? AND expiry_tier='TIER1'
      AND trade_date >= CAST(? AS DATE) - INTERVAL '252' DAY
    GROUP BY trade_date
)
SELECT
    AVG(iv) AS current_iv,
    (SELECT COUNT(*) FROM daily_iv WHERE avg_iv < current_iv)
    / NULLIF((SELECT COUNT(*) FROM daily_iv), 0) * 100.0 AS ivp
FROM options_data WHERE trade_date=? AND snap_time=? ...
```

> **Critical**: `IVP=0` is a valid reading (current IV is the lowest in history). All code uses explicit `if ivp is not None` checks — never `ivp or 100` which would wrongly treat IVP=0 as missing.

### 4.3 IV Term Structure

Compares ATM IV across expiry tiers to determine term structure shape:

| Condition | Shape | Meaning |
|---|---|---|
| IV(TIER1) < IV(TIER2) | `CONTANGO` | Near-term cheaper; normal market |
| IV(TIER1) ≈ IV(TIER2) | `FLAT` | No term premium |
| IV(TIER1) > IV(TIER2) | `BACKWARDATION` | Near-term crisis premium; dangerous for buyers |

---

## 5. VEX/CEX — Vanna & Charm Exposure (`analytics/vex_cex.py`)

### 5.1 Vanna Exposure (VEX)

**Vanna** = d(Delta)/d(IV). When IV changes, dealers must re-hedge their delta.

```
VEX = SUM(vex_column) / 1,000,000   [expressed in ₹ millions]
```

| VEX Total | Signal | Mechanical Effect |
|---|---|---|
| `> VEX_BULL_THRESHOLD` | `VEX_BULLISH` | IV drop forces dealer buying (upward pressure) |
| `< 0` | `VEX_BEARISH` | IV rise forces dealer selling (downward pressure) |
| Near zero | `NEUTRAL` | No dominant vanna flow |

### 5.2 Charm Exposure (CEX)

**Charm** = d(Delta)/d(time). As time decays, dealers re-hedge their delta.

```
CEX = SUM(cex_column) / 1,000,000   [expressed in ₹ millions]
```

| CEX Total | Signal |
|---|---|
| `>= CEX_STRONG_BID` | `STRONG_CHARM_BID` — strong time-decay buying |
| `>= CEX_BID` | `CHARM_BID` |
| `<= CEX_PRESSURE` | `CHARM_PRESSURE` |
| Between | `NEUTRAL` |

### 5.3 Dealer O’Clock

A special high-risk condition when DTE=1 and time approaches expiry:

```python
def _is_dealer_oclock(snap_time: str, dte: int) -> bool:
    return dte <= settings.DEALER_OCLOCK_DTE and snap_time >= settings.DEALER_OCLOCK_START
```

When active:
- Charm flows dominate all other signals
- Gate C10 condition fails (1 point deducted)
- Narrative warns explicitly
- Pre-flight may block recommendation

### 5.4 By-Strike Breakdown

For the full VEX/CEX panel, `_get_by_strike()` uses a window function to compute moneyness:

```sql
SELECT strike_price, option_type,
    (strike_price - AVG(spot) OVER()) / AVG(spot) OVER() * 100 AS moneyness_pct,
    SUM(vex)/1e6 AS vex_M, SUM(cex)/1e6 AS cex_M,
    SUM(oi) AS oi, AVG(iv) AS iv, MIN(dte) AS dte
FROM options_data
WHERE ... GROUP BY strike_price, option_type ORDER BY strike_price
```

---

## 6. Strike Screener (`analytics/screener.py`)

### 6.1 S-Score Composite

The screener ranks all TIER1 strikes by a composite **S-Score** that measures option quality for buying:

```
S_Score = W_LIQUIDITY    × liquidity_score
        + W_IV_QUALITY   × iv_quality_score
        + W_DELTA_QUALITY × delta_quality_score
        + W_SPREAD       × spread_score
        + W_EFF_RATIO    × efficiency_ratio
```

All weights are configurable via settings.

### 6.2 Component Calculations

| Component | Formula | What It Measures |
|---|---|---|
| Liquidity | `volume / (oi + 1)` | Turnover rate — how actively traded |
| IV Quality | `1 / (1 + iv)` | Lower IV = cheaper premium |
| Delta Quality | `1 - abs(delta - target_delta)` | Proximity to target delta (e.g. 0.35) |
| Spread | `1 - (ask_qty - bid_qty) / (ask_qty + bid_qty + 1)` | Bid-ask balance |
| Efficiency Ratio | `abs(delta) / (theta * sqrt(dte + 1))` | Delta per unit of time decay |

### 6.3 SQL Query

```sql
SELECT strike_price, option_type, ltp, iv, delta, theta, gamma, vega, dte,
       expiry_date, oi, volume,
       (
         ? * volume / (oi + 1) +
         ? * 1.0/(1.0 + iv) +
         ? * (1.0 - ABS(delta - ?)) +
         ? * (1.0 - ABS(bid_qty - ask_qty) / NULLIF(bid_qty + ask_qty, 0)) +
         ? * ABS(delta) / NULLIF(ABS(theta) * SQRT(dte + 1), 0)
       ) AS s_score
FROM options_data
WHERE trade_date=? AND snap_time=? AND underlying=?
  AND expiry_tier='TIER1'
  AND ltp > 0 AND oi > 0 AND delta IS NOT NULL
ORDER BY s_score DESC
LIMIT ?
```

Returns top `N` strikes (default 20, configurable 5–50) sorted by S-Score descending.

---

## 7. Microstructure (`analytics/microstructure.py`)

### 7.1 Volume Velocity

Detects unusual volume spikes using a rolling median:

```sql
SELECT snap_time,
    SUM(volume) AS total_volume
FROM options_data
WHERE trade_date=? AND underlying=? AND expiry_tier='TIER1'
  AND snap_time <= ?
GROUP BY snap_time
ORDER BY snap_time
```

```python
rolling_median = median(last_N_volumes)   # N = VOLUME_LOOKBACK_SNAPS
volume_ratio   = current_volume / (rolling_median or 1)

signal = "SPIKE"  if volume_ratio >= VOLUME_SPIKE_RATIO else "NORMAL"
```

### 7.2 Order Flow Toxicity

A high bid-ask spread combined with high volume indicates informed trading (toxic flow):

```python
# If OBI is extreme AND volume is spiking — likely institutional block
toxic = abs(obi) > OBI_TOXICITY_THRESHOLD and signal == "SPIKE"
```

---

## 8. Analytics Call Hierarchy

All analytics functions called by `get_environment_score()` in a single tick:

```
get_environment_score()
    ├─ get_net_gex()           ── 1 DuckDB query
    ├─ get_coc_latest()        ── 1 DuckDB query
    │   └─ [internal] 15m lag  ── 1 DuckDB query
    ├─ get_ivr_ivp()           ── 2 DuckDB queries (current + CTE history)
    ├─ get_pcr()               ── 1 DuckDB query
    │   └─ _smoothed_obi()     ── 1 DuckDB query
    ├─ get_vex_cex_current()   ── 1 DuckDB query
    ├─ get_atm_obi()           ── 1 DuckDB query
    └─ get_futures_obi()       ── 1 DuckDB query
                                  ───────────────
                                  ~9 DuckDB queries per gate evaluation
```

All queries are parameterised, lightweight columnar scans. On typical intraday data (~75 snaps, ~5000 rows per snap) each query completes in <10ms.
