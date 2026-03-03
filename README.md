# OptDash v2

**Options Analytics & AI Trading Engine** for NIFTY / BANKNIFTY

---

## Architecture

```
optdash/
├── config.py              ← All settings (pydantic-settings)
├── models/                ← Enums and data models
├── pipeline/              ← Data ingestion & DuckDB loading
├── analytics/             ← 10 analytics modules (GEX, CoC, IV, PCR, VEX/CEX …)
├── ai/
│   ├── direction.py         ← Weighted signal voting
│   ├── confidence.py        ← 4-bucket confidence scorer
│   ├── narrative.py         ← Template-based trade narrative
│   ├── pre_flight.py        ← 8 hard blocking rules
│   ├── quality.py           ← A/B/C/D quality grade
│   ├── recommender.py       ← Full recommendation orchestrator
│   ├── tracker.py           ← Live position tracker
│   ├── shadow_tracker.py    ← Hypothetical rejected-trade tracking
│   ├── eod.py               ← EOD force-close sweep
│   ├── journal/             ← SQLite DAOs (trades, snaps, shadow)
│   └── learning/            ← Performance stats & learning report
├── api/
│   ├── app.py               ← FastAPI factory
│   ├── deps.py              ← DB dependency injection
│   └── routers/             ← market, micro, screener, ai, ws
└── scheduler.py           ← APScheduler tick (every 5 min)
frontend/                  ← Vite + React 18 + TypeScript + Recharts
```

---

## Quick Start

```bash
# 1. Install backend
pip install -e ".[dev]"

# 2. Copy env template and configure
cp .env.example .env

# 3. Start API + scheduler
python run_api.py

# 4. Start frontend
cd frontend && npm install && npm run dev
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DUCKDB_PATH` | `data/optdash.duckdb` | DuckDB data file |
| `JOURNAL_DB_PATH` | `data/journal.db` | SQLite journal |
| `API_HOST` | `0.0.0.0` | API bind host |
| `API_PORT` | `8000` | API port |
| `UNDERLYINGS` | `NIFTY,BANKNIFTY` | Comma-separated underlyings |

---

## Backend Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/market/spot` | Latest spot price |
| GET | `/api/market/gex` | GEX series |
| GET | `/api/market/coc` | CoC series |
| GET | `/api/market/environment` | 11-point environment gate |
| GET | `/api/micro/pcr` | PCR series |
| GET | `/api/micro/alerts` | Live alerts |
| GET | `/api/micro/volume-velocity` | Volume velocity |
| GET | `/api/micro/vex-cex` | VEX/CEX series |
| GET | `/api/screener/strikes` | Top-N strikes by S_score |
| GET | `/api/screener/term-structure` | IV term structure |
| GET | `/api/ai/recommendation/latest` | Latest pending trade card |
| GET | `/api/ai/position/live` | Live open position |
| POST | `/api/ai/accept` | Accept recommendation |
| POST | `/api/ai/reject` | Reject recommendation |
| POST | `/api/ai/close-trade` | Manual close |
| GET | `/api/ai/journal/history` | Paginated trade history |
| GET | `/api/ai/learning/report` | Learning analytics |
| WS | `/ws/live` | Live snap WebSocket feed |

---

## Data Flow

```
NSE Raw Data → Pipeline → DuckDB
                                ↓
                         Analytics Modules
                                ↓
                        Scheduler (every 5 min)
                                ↓
              Recommender → Journal DB (SQLite)
                                ↓
                         FastAPI → Frontend
```
