# OptDash v2

> Options Market Intelligence Dashboard — Real-time GEX, CoC, VEX/CEX, PCR, Strike Screener + AI Trade Recommender

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI, DuckDB (analytics), SQLite (journal) |
| Scheduler | APScheduler — 5-min tick pipeline |
| AI Engine | Deterministic rules + formula — zero LLM calls |
| Frontend | React 18, TypeScript, Vite 5, TanStack Query v5, Zustand, Recharts, Tailwind CSS |

## Project Structure

```
optdash/
  analytics/       # GEX, CoC, VEX/CEX, PCR, IV, environment gate, screener
  ai/              # Direction bias, confidence, pre-flight, recommender, tracker
  ai/journal/      # SQLite trade journal — schema + DAOs
  ai/learning/     # Win rate, calibration, signal accuracy, regret analysis
  api/             # FastAPI app, routers, Pydantic schemas
  pipeline/        # DuckDB gateway, Parquet ingestion, APScheduler
frontend/          # React 18 + TypeScript trading terminal
```

## Quick Start

```bash
# 1. Backend
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .
cp .env.example .env   # set DATA_ROOT
python run_api.py

# 2. Frontend (new terminal)
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`

## Configuration

Create `.env` in project root:
```
DATA_ROOT=C:/path/to/your/parquet/data
JOURNAL_DB=journals/optdash.db
LOG_LEVEL=INFO
```

## API Docs

FastAPI auto-docs available at `http://localhost:8000/docs` when running.
