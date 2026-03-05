# OptDash — Part 10: API Layer

The API layer is a FastAPI application that exposes all analytics, screener, position, and AI engine functions as HTTP endpoints. It is the sole bridge between the Python backend and the React frontend.

---

## 1. Design Contract

1. **Read-only for market data.** All `market`, `micro`, and `screener` routers call analytics functions — they never write.
2. **Journal-aware.** All `ai` router endpoints read/write the SQLite journal via `get_journal` dependency.
3. **No cross-contamination.** API imports from both `analytics/` and `ai/`; neither imports from `api/`.
4. **`trade_date` filter first.** Every DuckDB query passes `trade_date` as the first filter for Parquet pruning efficiency.
5. **`COALESCE(ltp, closeprice, close)` everywhere.** LTP may be null; always use the COALESCE pattern.
6. **`q_safe` for optional columns.** `vexk`, `cexk` columns may not exist in older Parquet files. Use `q_safe` to return `[]` instead of a 500 error.

---

## 2. File Map

```
optdash/api/
    __init__.py
    main.py              ← FastAPI app factory + lifespan
    deps.py              ← get_duckdb, get_journal, q_safe
    ws.py                ← WebSocket position event broadcast
    schemas/
        __init__.py
        requests.py      ← AcceptRequest, RejectRequest, CloseRequest, ManualCloseRequest
        responses.py     ← TradeCard, PositionLive, LearningReport, all panel schemas
    routers/
        __init__.py
        market.py        ← /market — spot, gex, coc, environment
        microstructure.py← /micro  — pcr, alerts, volume-velocity, vex-cex
        screener.py      ← /screener — strikes, ivp, term-structure
        position.py      ← /position — theta-sl-series, pnl-attribution
        ai.py            ← /ai — recommend, accept, reject, close-trade, trades
```

---

## 3. `api/main.py` — App Factory

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────
    from optdash.pipeline.duckdbgateway import startup
    from optdash.journal.schema import init_db, run_migrations, journal_conn

    app.state.duckdb = startup()          # registers DuckDB views
    with journal_conn() as conn:
        init_db(conn)
        run_migrations(conn)
    yield
    # ── Shutdown ─────────────────────────────────────────
    app.state.duckdb.close()

app = FastAPI(
    title="OptDash API",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],   # Vite dev server
    allow_methods=["*"],
    allow_headers=["*"],
)

# Router registration
from optdash.api.routers import market, microstructure, screener, position, ai
app.include_router(market.router,          prefix="/market")
app.include_router(microstructure.router,  prefix="/micro")
app.include_router(screener.router,        prefix="/screener")
app.include_router(position.router,        prefix="/position")
app.include_router(ai.router,              prefix="/ai")
```

`runapi.py` entry point:
```python
import uvicorn
if __name__ == "__main__":
    uvicorn.run("optdash.api.main:app", host="127.0.0.1", port=8000, reload=False)
```

---

## 4. `api/deps.py` — Dependencies

### 4.1 `get_duckdb`

Returns the single shared DuckDB connection stored on `app.state`.

```python
import duckdb
from fastapi import Request

def get_duckdb(request: Request) -> duckdb.DuckDBPyConnection:
    """
    DuckDB is NOT thread-safe.
    The API runs single-threaded (uvicorn workers=1) so one shared connection is safe.
    For multi-worker deployments, use connection-per-request instead.
    """
    return request.app.state.duckdb
```

### 4.2 `get_journal`

Opens a per-request SQLite connection with WAL mode and FK enforcement. Yields, then commits or rolls back.

```python
import sqlite3
from typing import Generator
from optdash.config import settings

def get_journal() -> Generator[sqlite3.Connection, None, None]:
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

### 4.3 `q_safe`

Executes a DuckDB query and returns a list of dicts. If a column referenced in the query does not exist (older Parquet files without `vexk`/`cexk`), returns `[]` instead of raising a 500 error.

```python
def q_safe(
    conn: duckdb.DuckDBPyConnection,
    query: str,
    params: tuple = (),
) -> list[dict]:
    try:
        rows = conn.execute(query, list(params)).fetchall()
        cols = [d[0] for d in conn.description]
        return [dict(zip(cols, row)) for row in rows]
    except Exception as e:
        if "column" in str(e).lower() or "Binder Error" in str(e):
            return []
        raise
```

---

## 5. `api/ws.py` — WebSocket Broadcast

Broadcasts position change events to all connected frontend clients. The Position Monitor panel subscribes to this stream.

```python
from fastapi import WebSocket
from typing import Any
import asyncio, json

class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)

    async def broadcast(self, data: Any):
        msg = json.dumps(data)
        for ws in list(self.active):
            try:
                await ws.send_text(msg)
            except Exception:
                self.disconnect(ws)

manager = ConnectionManager()
```

Event types broadcast:

| Event type | Trigger |
|---|---|
| `TRADE_GENERATED` | New recommendation written |
| `TRADE_ACCEPTED` | User accepted a trade |
| `TRADE_REJECTED` | User rejected a trade |
| `TRADE_CLOSED` | Trade closed (any reason) |
| `POSITION_SNAP` | Tracker wrote a new position snap |

WebSocket endpoint in `ai.py`:
```
WS /ai/ws
```

---

## 6. `api/schemas/requests.py`

```python
from pydantic import BaseModel

class AcceptRequest(BaseModel):
    trade_id:       int
    entry_premium:  float
    entry_spot:     float
    entry_snap_time: str
    entry_iv:       float
    entry_delta:    float
    entry_theta:    float
    entry_gamma:    float
    entry_vega:     float

class RejectRequest(BaseModel):
    trade_id:         int
    rejection_reason: str     # RejectionReason enum value

class ManualCloseRequest(BaseModel):
    trade_id:      int
    exit_premium:  float
    exit_spot:     float
    exit_snap_time: str
```

---

## 7. `api/schemas/responses.py`

All response schemas are Pydantic `BaseModel` subclasses. Fields map directly to the TypeScript interfaces in `frontend/src/types/`.

### 7.1 Market Schemas

```python
class SpotData(BaseModel):
    snap_time:  str
    spot:       float
    day_open:   float
    day_high:   float
    day_low:    float
    change_pct: float

class GEXRow(BaseModel):
    snap_time:   str
    gex_all_B:   float
    gex_near_B:  float
    gex_far_B:   float
    pct_of_peak: float
    regime:      str          # POSITIVE_CHOP | NEGATIVE_TREND

class CoCRow(BaseModel):
    snap_time:  str
    fut_price:  float
    spot:       float
    coc:        float
    vcoc_15m:   float
    signal:     str           # VELOCITY_BULL | VELOCITY_BEAR | DISCOUNT | NORMAL

class ConditionDetail(BaseModel):
    met:       bool
    value:     float | str | None
    threshold: str
    points:    int
    note:      str
    is_bonus:  bool = False

class EnvironmentScore(BaseModel):
    score:      int
    maxscore:   int
    verdict:    str           # GO | WAIT | NOGO
    conditions: dict[str, ConditionDetail]
```

### 7.2 Screener Schemas

```python
class StrikeRow(BaseModel):
    expiry_date:  str
    dte:          int
    expiry_tier:  str
    option_type:  str
    strike_price: float
    ltp:          float | None
    iv:           float | None
    delta:        float | None
    theta:        float | None
    gamma:        float | None
    vega:         float | None
    moneyn_pct:   float | None
    rho:          float | None
    eff_ratio:    float | None
    sscore:       float | None
    stars:        int

class IVPResponse(BaseModel):
    underlying:  str
    snap_time:   str
    ivp:         float
    ivr:         float
    atm_iv:      float
    hv20:        float
    iv_hv_spread: float
    vol_regime:  str           # RICH | FAIR | CHEAP

class TermStructureRow(BaseModel):
    expiry_date: str
    dte:         int
    expiry_tier: str
    atm_iv:      float
    avg_theta:   float
    shape:       str           # CONTANGO | FLAT | BACKWARDATION
```

### 7.3 Microstructure Schemas

```python
class PCRRow(BaseModel):
    snap_time:     str
    pcr_vol:       float
    pcr_oi:        float
    pcr_divergence: float
    smoothed_obi:  float
    signal:        str         # RETAIL_PANIC_PUTS | DIVERGENCE_BUILDING | RETAIL_PANIC_CALLS | BALANCED

class AlertItem(BaseModel):
    time:      str
    type:      str             # AlertType enum value
    severity:  str             # HIGH | MEDIUM | LOW
    direction: str | None
    headline:  str
    message:   str

class VolumeVelocityRow(BaseModel):
    snap_time:    str
    vol_total:    float
    baseline_vol: float
    vol_ratio:    float
    signal:       str          # EXTREME_SURGE | SPIKE | ELEVATED | NORMAL

class VexCexSeriesRow(BaseModel):
    snap_time:    str
    vex_total_M:  float
    vex_ce_M:     float
    vex_pe_M:     float
    cex_total_M:  float
    cex_ce_M:     float
    cex_pe_M:     float
    spot:         float
    dte:          int
    vex_signal:   str
    cex_signal:   str
    dealer_oclock: bool
    interpretation: str

class VexCexStrikeRow(BaseModel):
    strike_price: float
    option_type:  str
    moneyn_pct:   float
    vex_M:        float
    cex_M:        float
    oi:           float
    iv:           float
    dte:          int

class VexCexResponse(BaseModel):
    series:        list[VexCexSeriesRow]
    by_strike:     list[VexCexStrikeRow]
    current:       VexCexSeriesRow | None
    dealer_oclock: bool
    interpretation: str
```

### 7.4 Position Schemas

```python
class ThetaSLPoint(BaseModel):
    snap_time:     str
    entry_premium: float
    theta_daily:   float
    sl_base:       float
    sl_adjusted:   float       # rises as time passes
    current_ltp:   float
    unrealised_pnl: float
    pnl_pct:       float
    status:        str         # IN_TRADE | STOP_HIT | PROFIT_ZONE_PARTIAL_EXIT | GUARANTEED_PROFIT_ZONE

class PnLAttributionRow(BaseModel):
    snap_time:       str
    ltp:             float
    spot:            float
    delta_pnl:       float
    gamma_pnl:       float
    vega_pnl:        float
    theta_pnl:       float
    actual_pnl:      float
    theoretical_pnl: float
    unexplained:     float
```

### 7.5 AI / Trade Schemas

```python
class TradeCard(BaseModel):
    id:               int
    trade_date:       str
    created_at:       str
    underlying:       str
    direction:        str
    expiry_date:      str
    dte:              int
    expiry_tier:      str
    strike:           float
    option_type:      str
    entry_premium:    float | None
    entry_spot:       float | None
    sl:               float
    target:           float
    status:           str
    exit_reason:      str | None
    rejection_reason: str | None
    shadow_outcome:   str | None
    pnl_pts:          float | None
    pnl_pct:          float | None
    confidence:       int
    gate_score:       int
    session:          str
    narrative:        str
    signals:          list[str]

class LearningReport(BaseModel):
    underlying:           str
    lookback_days:        int
    stats:                dict
    calibration_bands:    list[dict]
    directional_accuracy: dict
    regret_summary:       dict
    time_performance:     list[dict]
    suggestions:          list[dict]
```

---

## 8. `api/routers/market.py`

Prefix: `/market` | DuckDB dependency: `Depends(get_duckdb)`

| Method | Path | Query Params | Analytics Call | Response |
|---|---|---|---|---|
| GET | `/spot` | `trade_date`, `underlying` | `analytics.regime` (spot series) | `list[SpotData]` |
| GET | `/gex` | `trade_date`, `underlying` | `analytics.gex.get_gex_series()` | `list[GEXRow]` |
| GET | `/coc` | `trade_date`, `underlying` | `analytics.coc.get_coc_series()` | `list[CoCRow]` |
| GET | `/environment` | `trade_date`, `snap_time`, `underlying`, `direction?` | `analytics.environment.get_environment_score()` | `EnvironmentScore` |

```python
from fastapi import APIRouter, Depends, Query
from optdash.api.deps import get_duckdb

router = APIRouter(tags=["market"])

@router.get("/environment", response_model=EnvironmentScore)
def get_environment(
    trade_date: str = Query(...),
    snap_time:  str = Query(...),
    underlying: str = Query(...),
    direction:  str | None = Query(default=None),
    conn = Depends(get_duckdb),
):
    from optdash.analytics.environment import get_environment_score
    return get_environment_score(conn, trade_date, snap_time, underlying, direction)
```

---

## 9. `api/routers/microstructure.py`

Prefix: `/micro` | DuckDB dependency

| Method | Path | Query Params | Analytics Call | Response |
|---|---|---|---|---|
| GET | `/pcr` | `trade_date`, `underlying` | `analytics.pcr.get_pcr_series()` | `list[PCRRow]` |
| GET | `/alerts` | `trade_date`, `snap_time`, `underlying` | `analytics.alerts.get_alerts()` | `list[AlertItem]` |
| GET | `/volume-velocity` | `trade_date`, `underlying` | `analytics.microstructure.get_volume_velocity()` | `list[VolumeVelocityRow]` |
| GET | `/vex-cex` | `trade_date`, `underlying`, `snap_time` | `analytics.vexcex.get_vex_cex_series()` | `VexCexResponse` |

All alert queries use a LAG-based DuckDB window function to fire only on **transition**, not every snap.

---

## 10. `api/routers/screener.py`

Prefix: `/screener` | DuckDB dependency

| Method | Path | Query Params | Analytics Call | Response |
|---|---|---|---|---|
| GET | `/strikes` | `trade_date`, `underlying`, `snap_time`, `top_n=20` | `analytics.screener.get_strikes()` | `list[StrikeRow]` |
| GET | `/ivp` | `trade_date`, `snap_time`, `underlying` | `analytics.iv.get_ivr_ivp()` | `IVPResponse` |
| GET | `/term-structure` | `trade_date`, `underlying`, `snap_time` | `analytics.iv.get_term_structure()` | `list[TermStructureRow]` |

---

## 11. `api/routers/position.py`

Prefix: `/position` | DuckDB + Journal dependencies

| Method | Path | Query Params | Source | Response |
|---|---|---|---|---|
| GET | `/theta-sl-series` | `trade_id` | `journal.snaps` + `analytics.pnl` | `list[ThetaSLPoint]` |
| GET | `/pnl-attribution` | `trade_id` | `journal.snaps` + `analytics.pnl` | `list[PnLAttributionRow]` |

Both endpoints reconstruct the per-snap series by joining position snap data with the DuckDB LTP for each `(trade_date, snap_time, underlying, strike, option_type)` combination.

```python
@router.get("/theta-sl-series", response_model=list[ThetaSLPoint])
def theta_sl_series(
    trade_id: int = Query(...),
    conn     = Depends(get_duckdb),
    jconn    = Depends(get_journal),
):
    from optdash.journal.snaps import get_position_snaps
    from optdash.analytics.pnl import compute_theta_sl_series
    snaps = get_position_snaps(jconn, trade_id)
    return compute_theta_sl_series(conn, trade_id, snaps)
```

---

## 12. `api/routers/ai.py`

Prefix: `/ai` | Both DuckDB and Journal dependencies

| Method | Path | Body / Query | Action | Response |
|---|---|---|---|---|
| GET | `/recommend` | `trade_date`, `snap_time`, `underlying` | Runs `recommender.generate_recommendation()` | `TradeCard \| null` |
| POST | `/accept` | `AcceptRequest` | Calls `trades.accept_trade()`, broadcasts WS event | `TradeCard` |
| POST | `/reject` | `RejectRequest` | Calls `trades.reject_trade()`, broadcasts WS event | `TradeCard` |
| POST | `/close` | `ManualCloseRequest` | Calls `trades.close_trade(exit_reason=MANUAL_EXIT)` | `TradeCard` |
| GET | `/open-trades` | `trade_date` | `trades.get_open_trades()` | `list[TradeCard]` |
| GET | `/trade-history` | `lookback_days=30` | `trades.get_closed_trades()` | `list[TradeCard]` |
| GET | `/learning` | `underlying`, `lookback_days=30` | Calls all learning modules | `LearningReport` |
| WS | `/ws` | — | WebSocket upgrade, position event stream | — |

The `/recommend` endpoint does **not** auto-write to the journal — it only runs the full recommendation pipeline and returns the result. The scheduler's 11-step loop handles actual journal writes via `recommender.generate_recommendation()`. The API `/recommend` is for on-demand UI refresh.

---

## 13. Polling Strategy

| Endpoint Group | Frontend Interval | Rationale |
|---|---|---|
| `/market/*`, `/micro/*` | 5 s | Live signals, gate, GEX, CoC |
| `/screener/*` | 30 s | Slower-moving; reduces server load |
| `/position/*` | On demand | User-triggered; no auto-refresh |
| `/ai/open-trades` | 10 s | Trade state changes |
| `/ai/ws` | Persistent | Push-based for instant state updates |

---

## 14. Sprint 5 — Files to Create

```
30. optdash/api/__init__.py
31. optdash/api/main.py
32. optdash/api/deps.py
33. optdash/api/ws.py
34. optdash/api/schemas/__init__.py
35. optdash/api/schemas/requests.py
36. optdash/api/schemas/responses.py
37. optdash/api/routers/__init__.py
38. optdash/api/routers/market.py
39. optdash/api/routers/microstructure.py
40. optdash/api/routers/screener.py
41. optdash/api/routers/position.py
42. optdash/api/routers/ai.py
43. runapi.py
```

### Checkpoint

```bash
# Start API in background
python runapi.py &
sleep 2

# Smoke test all routers
curl -s "http://127.0.0.1:8000/market/gex?trade_date=2026-02-28&underlying=NIFTY" | python -m json.tool | head -20
curl -s "http://127.0.0.1:8000/market/environment?trade_date=2026-02-28&snap_time=10:15&underlying=NIFTY" | python -m json.tool
curl -s "http://127.0.0.1:8000/screener/strikes?trade_date=2026-02-28&underlying=NIFTY&snap_time=10:15" | python -m json.tool | head -30
curl -s "http://127.0.0.1:8000/ai/open-trades?trade_date=2026-02-28" | python -m json.tool
curl -s "http://127.0.0.1:8000/ai/learning?underlying=NIFTY" | python -m json.tool

# All should return 200 with valid JSON (may be [] for empty journal)
echo 'API CHECKPOINT PASSED'
```
