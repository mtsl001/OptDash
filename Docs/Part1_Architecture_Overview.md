# OptDash — Part 1: Architecture & System Overview

---

## 1. Introduction

OptDash is a real-time options analytics and AI-powered trade recommendation engine built specifically for Indian equity derivatives markets (NSE). It processes live intraday options chain data every 5 minutes, computes multi-dimensional market signals, scores the trading environment, and generates fully-explained trade recommendations with dynamic stop-loss management.

The system is designed for a single retail/semi-professional trader who wants institutional-grade signal intelligence without needing to manually interpret 10+ data dimensions simultaneously.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          OptDash v2.0                               │
│                                                                     │
│  ┌──────────────┐    ┌──────────────────┐    ┌───────────────────┐ │
│  │  Parquet     │    │   DuckDB         │    │   FastAPI         │ │
│  │  Data Feed   │───▶│   In-Memory      │───▶│   REST + WS       │ │
│  │  (5-min)     │    │   Analytics      │    │   API Layer       │ │
│  └──────────────┘    └──────────────────┘    └───────────────────┘ │
│         │                    │                        │             │
│         │            ┌───────┴──────┐        ┌────────┴──────────┐ │
│         │            │  APScheduler │        │  WebSocket        │ │
│         │            │  5-min Tick  │        │  Live Feed        │ │
│         │            └───────┬──────┘        └────────┬──────────┘ │
│         │                    │                        │             │
│         │            ┌───────▼──────┐                 │             │
│         │            │  AI Engine   │                 │             │
│         │            │  Recommender │                 │             │
│         │            └───────┬──────┘                 │             │
│         │                    │                        │             │
│         │            ┌───────▼──────┐                 │             │
│         └───────────▶│  SQLite      │◀────────────────┘             │
│                       │  Journal DB  │                               │
│                       └─────────────┘                               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| Language | Python 3.11+ | Core runtime |
| Web Framework | FastAPI 0.111+ | REST API + WebSocket |
| ASGI Server | Uvicorn (standard) | Production server with WebSocket |
| Analytics DB | DuckDB 0.10.x–1.x | In-memory columnar analytics over Parquet |
| Journal DB | SQLite 3 (WAL mode) | Trade journal, positions, learning data |
| Scheduler | APScheduler 3.10+ | 5-minute market tick pipeline |
| Config | Pydantic-Settings 2.x | `.env`-based typed configuration |
| Logging | Loguru | Structured application logging |
| Options Math | py_vollib | Black-Scholes Greeks computation |
| Data Science | pandas, numpy, scipy | Data processing and statistics |
| Build | Hatchling | PEP 517 build backend |

---

## 4. Repository Structure

```
OptDash/
├── optdash/
│   ├── __init__.py
│   ├── config.py                  # All settings via Pydantic-Settings
│   ├── scheduler.py               # App-level APScheduler startup
│   ├── models/
│   │   ├── __init__.py
│   │   └── enums.py               # 14 enumerations (Direction, TradeStatus, etc.)
│   ├── pipeline/
│   │   ├── duckdb_gateway.py      # DuckDB :memory: + Parquet view registration
│   │   ├── ingestion.py           # Snap/date/underlying resolution utilities
│   │   └── scheduler.py           # Pipeline tick: track → recommend → EOD
│   ├── analytics/
│   │   ├── gex.py                 # Gamma Exposure (GEX) analytics
│   │   ├── coc.py                 # Cost-of-Carry (CoC) + OBI
│   │   ├── pcr.py                 # Put-Call Ratio divergence
│   │   ├── iv.py                  # IV rank, IV percentile, term structure
│   │   ├── vex_cex.py             # Vanna/Charm Exposure
│   │   ├── screener.py            # Strike screener with S-score
│   │   ├── microstructure.py      # Volume velocity, order flow
│   │   ├── environment.py         # 11-point Environment Gate
│   │   └── alerts.py              # Transition-based signal alerts
│   ├── ai/
│   │   ├── direction.py           # Directional bias scoring
│   │   ├── confidence.py          # 4-bucket confidence score
│   │   ├── pre_flight.py          # Hard-rule pre-flight checks
│   │   ├── quality.py             # Trade quality grade (A/B/C/D)
│   │   ├── narrative.py           # Template-based trade narrative
│   │   ├── recommender.py         # Full recommendation orchestrator
│   │   ├── tracker.py             # Live position tracking (every tick)
│   │   ├── shadow_tracker.py      # Shadow (rejected) trade tracking
│   │   ├── eod.py                 # End-of-day force close + finalization
│   │   ├── journal/
│   │   │   ├── schema.py          # SQLite DDL: tables + indexes
│   │   │   ├── trades.py          # Trades DAO (CRUD)
│   │   │   ├── snaps.py           # Position snaps DAO
│   │   │   └── shadow.py          # Shadow trades DAO
│   │   └── learning/
│   │       ├── stats.py           # Performance statistics
│   │       └── report.py          # Comprehensive learning report
│   └── api/
│       ├── deps.py                # FastAPI dependencies (DuckDB + SQLite)
│       └── routers/
│           ├── market.py          # /market/* endpoints
│           ├── screener.py        # /screener/* endpoints
│           ├── ai.py              # /ai/* endpoints
│           └── ws.py              # WebSocket live feed
├── pyproject.toml
├── .env.example
└── Docs/
    ├── Part1_Architecture_Overview.md      (this file)
    ├── Part2_Data_Pipeline.md
    ├── Part3_Analytics_Engine.md
    ├── Part4_AI_Engine.md
    ├── Part5_Trade_Lifecycle.md
    ├── Part6_Position_Tracking_Alerts.md
    ├── Part7_Environment_Gate.md
    ├── Part8_Learning_Engine.md
    ├── Part9_API_Reference.md
    └── Part10_Configuration_Deployment.md
```

---

## 5. Two-Database Architecture

OptDash uses **two completely separate databases** for two distinct concerns:

### 5.1 DuckDB — Columnar Analytics (Read-Only Market Data)

- **Connection type**: In-process `:memory:` with Parquet glob views
- **Data source**: Parquet files in `DATA_ROOT/**/*.parquet`
- **Access pattern**: Read-only analytical queries (GROUP BY, window functions, aggregations)
- **Shared**: Same connection instance used by BOTH the API layer and the scheduler
- **Schema**: Single view `options_data` — see Section 6
- **Lifecycle**: Created at app startup via `duckdb_gateway.startup()`, closed at shutdown

### 5.2 SQLite — Journal Database (Read-Write State)

- **Connection type**: File-based, WAL mode, `check_same_thread=False`
- **Data source**: `JOURNAL_DB_PATH` (default: `data/journal.db`)
- **Access pattern**: Read-write CRUD — trade lifecycle, position snaps, learning data
- **Tables**: `trades`, `position_snaps`, `shadow_trades` (+ indexes)
- **Concurrency**: WAL mode allows simultaneous scheduler writes + API reads/writes
- **Pragmas**: `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`
- **Lifecycle**: Opened in `deps.startup()`, schema auto-migrated via `init_db()`

---

## 6. `options_data` Parquet Schema

All analytics queries operate against a single DuckDB view `options_data` that wraps Parquet files. The expected columns are:

| Column | Type | Description |
|---|---|---|
| `trade_date` | VARCHAR | Trading date `YYYY-MM-DD` (Hive partition key) |
| `snap_time` | VARCHAR | Snapshot time `HH:MM` (every 5 min) |
| `underlying` | VARCHAR | Index name: `NIFTY`, `BANKNIFTY`, etc. |
| `expiry_date` | VARCHAR | Option expiry date `YYYY-MM-DD` |
| `expiry_tier` | VARCHAR | `TIER1` (nearest weekly), `TIER2`, `TIER3` |
| `strike_price` | DOUBLE | Strike price |
| `option_type` | VARCHAR | `CE` or `PE` |
| `ltp` | DOUBLE | Last traded price |
| `bid_qty` | DOUBLE | Best bid quantity |
| `ask_qty` | DOUBLE | Best ask quantity |
| `volume` | DOUBLE | Volume in current snap |
| `oi` | DOUBLE | Open interest |
| `iv` | DOUBLE | Implied volatility (annualised, decimal) |
| `delta` | DOUBLE | Option delta |
| `gamma` | DOUBLE | Option gamma |
| `theta` | DOUBLE | Option theta (daily) |
| `vega` | DOUBLE | Option vega |
| `spot` | DOUBLE | Underlying spot price at snap |
| `futures_price` | DOUBLE | Nearest futures price |
| `dte` | INTEGER | Days to expiry |
| `vex` | DOUBLE | Vanna Exposure (pre-computed) |
| `cex` | DOUBLE | Charm Exposure (pre-computed) |
| `day_open` | DOUBLE | Day opening spot (optional) |
| `day_high` | DOUBLE | Day high spot (optional) |
| `day_low` | DOUBLE | Day low spot (optional) |

> **Note**: `union_by_name=true` is set in the Parquet view so older files missing `vex`/`cex` columns are handled gracefully via `_safe_query()` in `ingestion.py`.

---

## 7. Application Startup Sequence

```
App Start (uvicorn)
       │
       ▼
[1] FastAPI lifespan begins
       │
       ▼
[2] duckdb_gateway.startup()
    ├─ Create :memory: DuckDB connection
    ├─ PRAGMA threads=4
    ├─ PRAGMA memory_limit='2GB'
    └─ Register options_data view over DATA_ROOT/**/*.parquet
       │
       ▼
[3] deps.startup(app)
    ├─ Wire app.state.duck = get_duck_conn()  ← shared gateway connection
    ├─ Open SQLite journal (WAL + FK)
    ├─ init_db() — create tables/indexes if not exist
    └─ app.state.journal = jconn
       │
       ▼
[4] scheduler.startup()
    ├─ Create APScheduler BackgroundScheduler (IST timezone)
    ├─ Add market_tick job: CronTrigger(minute=*/5, hour=9-15, mon-fri)
    │   └─ max_instances=1, coalesce=True
    └─ scheduler.start()
       │
       ▼
[5] App ready — accepting requests
       │
       ▼ (every 5 min, 09:15–15:30 IST, Mon-Fri)
[6] _run_tick(conn, jconn)
    ├─ track_open_positions()
    ├─ track_shadow_positions()
    ├─ expire_stale_recommendations()
    ├─ generate_recommendation() × 5 underlyings
    ├─ eod_force_close()     [at EOD_FORCE_CLOSE_TIME exactly]
    └─ finalize_all_shadows() [at EOD_SWEEP_TIME exactly]
```

---

## 8. Supported Underlyings

The scheduler processes five underlyings every tick:

| Underlying | Full Name | Exchange |
|---|---|---|
| `NIFTY` | Nifty 50 | NSE |
| `BANKNIFTY` | Bank Nifty | NSE |
| `FINNIFTY` | Nifty Financial Services | NSE |
| `MIDCPNIFTY` | Nifty Midcap Select | NSE |
| `NIFTYNXT50` | Nifty Next 50 | NSE |

All analytics are computed independently per underlying.

---

## 9. Market Sessions

The trading day is divided into five named sessions, used for confidence adjustments and learning analytics:

| Session | Default Time Range | Characteristic |
|---|---|---|
| `OPENING` | 09:15 – 10:15 | High volatility, gap moves |
| `MIDMORNING` | 10:15 – 11:30 | Trend establishment |
| `MIDDAY_CHOP` | 11:30 – 13:00 | Low momentum, range-bound |
| `AFTERNOON` | 13:00 – 14:30 | Re-evaluation, global cues |
| `CLOSING_CRUSH` | 14:30 – 15:30 | Theta acceleration, expiry positioning |

Session boundaries are fully configurable via `.env` (`SESSION_OPENING_END`, `SESSION_MIDDAY_START`, etc.).

---

## 10. Data Flow Summary

```
Parquet Files (DATA_ROOT)
        │
        ▼
DuckDB :memory: view (options_data)
        │
        ├──▶ Analytics Layer (GEX / CoC / PCR / IV / VEX-CEX / Microstructure)
        │           │
        │           ▼
        │    Environment Gate (11-point score)
        │           │
        │           ▼
        │    AI Engine:
        │    Direction → Confidence → Pre-Flight → Quality → Narrative
        │           │
        │           ▼
        │    Trade Recommendation (GENERATED)
        │           │
        │           ▼
        │    SQLite Journal (trades table)
        │           │
        │    ┌──────┴──────────────────────┐
        │    │                             │
        │    ▼                             ▼
        │  ACCEPTED                    REJECTED
        │    │                             │
        │    ▼                             ▼
        │  Live Tracker             Shadow Tracker
        │  (every tick)             (every tick)
        │    │                             │
        │    ▼                             ▼
        │  position_snaps           shadow_trades
        │    │
        │    ▼
        │  Auto-close (SL/Target/Gate/IV/EOD)
        │    │
        │    ▼
        │  CLOSED → Learning Stats → Report
        │
        └──▶ API / WebSocket → Frontend Dashboard
```

---

## 11. Key Design Principles

1. **No external API calls in analytics** — all computation runs against local Parquet files via DuckDB
2. **Single shared DuckDB connection** — API and scheduler use the same in-memory connection to ensure data consistency
3. **Parameterised queries throughout** — all DuckDB and SQLite queries use `?` or `$1` parameter binding
4. **Explicit None guards** — IVP=0, GATE_MAX_SCORE=0, zero LTP all handled explicitly
5. **Idempotent schema init** — `init_db()` uses `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`
6. **Per-step error isolation** — scheduler tick wraps each step in try/except; one failure never kills the tick
7. **No LLM dependency** — all narrative text is template-based with data-backed sentences
8. **Actual fill price tracking** — all PnL (intra-day and EOD) based on `actual_entry_price`, not recommended price
9. **One-shot EOD** — EOD functions fire exactly once using `==` time comparison, never `>=`
10. **Session-aware AI** — confidence scoring, gate scoring, and learning all segmented by market session
