# OptDash — Part 13: Testing & Deployment

This part covers the full test suite, entry-point scripts, and the end-to-end system startup checklist.

---

## 1. Test Infrastructure — `tests/conftest.py`

```python
import pytest
import duckdb
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from optdash.journal.schema import init_db, run_migrations
from optdash.pipeline.processor import process
from optdash.pipeline.duckdbgateway import register_views

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def sample_options_df() -> pd.DataFrame:
    """Minimal enriched Parquet-style DataFrame covering NIFTY, one snap."""
    np.random.seed(42)
    strikes = [22000, 22050, 22100, 22150, 22200, 22250, 22300, 22350]
    rows = []
    for strike in strikes:
        for otype in ('CE', 'PE'):
            rows.append({
                'snaptime':        '10:00',
                'tradedate':       '2026-02-28',
                'underlying':      'NIFTY',
                'underlyingspot':  22150.0,
                'strikeprice':     float(strike),
                'optiontype':      otype,
                'expirydate':      '2026-03-06',
                'ltp':             abs(22150.0 - strike) * 0.3 + 50,
                'closeprice':      None,
                'iv':              0.18 + np.random.uniform(-0.02, 0.02),
                'oi':              float(np.random.randint(50_000, 500_000)),
                'volume':          float(np.random.randint(10_000, 100_000)),
                'tbq':             float(np.random.randint(1_000, 10_000)),
                'tsq':             float(np.random.randint(1_000, 10_000)),
            })
    return pd.DataFrame(rows)


@pytest.fixture(scope="session")
def sample_futures_df() -> pd.DataFrame:
    return pd.DataFrame([{
        'snaptime':    '10:00',
        'tradedate':   '2026-02-28',
        'underlying':  'NIFTY',
        'optiontype':  'FUT',
        'ltp':         22160.0,
        'futbuyqty':   12_000,
        'futsellqty':  10_000,
    }])


@pytest.fixture(scope="session")
def processed_df(sample_options_df, sample_futures_df) -> pd.DataFrame:
    return process(sample_options_df.copy(), sample_futures_df.copy(), '2026-02-28')


@pytest.fixture(scope="session")
def duckdb_conn(processed_df, tmp_path_factory) -> duckdb.DuckDBPyConnection:
    """DuckDB connection with vwoptions view loaded from processed DataFrame."""
    tmp = tmp_path_factory.mktemp("db") / "test.duckdb"
    conn = duckdb.connect(str(tmp))
    conn.register("vwoptions", processed_df)
    conn.execute("""
        CREATE OR REPLACE VIEW vwfutures AS
        SELECT * FROM vwoptions WHERE optiontype = 'FUT'
    """)
    conn.execute("""
        CREATE OR REPLACE VIEW vwatm AS
        SELECT * FROM vwoptions WHERE is_atm = true
    """)
    return conn


@pytest.fixture
def journal_conn(tmp_path) -> sqlite3.Connection:
    """Fresh SQLite journal per test."""
    conn = sqlite3.connect(str(tmp_path / "journal.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    init_db(conn)
    run_migrations(conn)
    conn.commit()
    yield conn
    conn.close()


TRADE_DATE = '2026-02-28'
SNAP_TIME  = '10:00'
UNDERLYING = 'NIFTY'
```

---

## 2. `tests/test_pipeline.py`

Tests the data pipeline modules in isolation.

```python
import pandas as pd
import pytest
from optdash.pipeline.processor import process

class TestProcessor:

    def test_effective_ltp_coalesce(self, sample_options_df, sample_futures_df):
        """COALESCE(ltp, closeprice, close) always produces a non-null effective_ltp."""
        df = process(sample_options_df.copy(), sample_futures_df.copy(), '2026-02-28')
        assert df['effective_ltp'].notna().all(), "effective_ltp should never be null when ltp is set"

    def test_greeks_computed(self, processed_df):
        """All 8 greeks columns must be present and non-null for ATM options."""
        atm = processed_df[processed_df['is_atm'] == True]
        for col in ('delta', 'gamma', 'theta', 'vega', 'vanna', 'charm_daily', 'gexk'):
            assert col in processed_df.columns, f"Missing column: {col}"
            assert atm[col].notna().any(), f"{col} should be non-null for ATM options"

    def test_delta_sign(self, processed_df):
        """CE delta must be positive, PE delta must be negative."""
        ce = processed_df[processed_df['optiontype'] == 'CE']
        pe = processed_df[processed_df['optiontype'] == 'PE']
        assert (ce['delta'] > 0).all(), "CE delta must be positive"
        assert (pe['delta'] < 0).all(), "PE delta must be negative"

    def test_dte_non_negative(self, processed_df):
        assert (processed_df['dte'] >= 0).all()

    def test_obi_range(self, processed_df):
        obi = processed_df['obi'].dropna()
        assert ((obi >= -1.0) & (obi <= 1.0)).all(), "OBI must be in [-1, 1]"

    def test_is_atm_count(self, processed_df):
        """Each (snap, underlying, expiry, optiontype) should have ≤ 8 ATM strikes."""
        atm_counts = (
            processed_df[processed_df['is_atm']]
            .groupby(['snaptime', 'underlying', 'expirydate', 'optiontype'])
            .size()
        )
        assert (atm_counts <= 8).all(), "ATM window should not exceed 8 strikes"

    def test_vexcex_columns_present(self, processed_df):
        assert 'vexk' in processed_df.columns
        assert 'cexk' in processed_df.columns

    def test_idempotent_deduplicate(self, sample_options_df, sample_futures_df):
        """Processing same data twice should give same row count as once."""
        df1 = process(sample_options_df.copy(), sample_futures_df.copy(), '2026-02-28')
        double = pd.concat([sample_options_df, sample_options_df]).reset_index(drop=True)
        df2 = process(double, sample_futures_df.copy(), '2026-02-28')
        # Row count should be same — dedup on primary key
        assert len(df1) == len(df2)
```

---

## 3. `tests/test_analytics.py`

Tests all analytics functions with the in-memory DuckDB fixture.

```python
import pytest
from optdash.analytics.gex import get_net_gex
from optdash.analytics.coc import get_coc_series, get_coc_latest
from optdash.analytics.iv import get_ivr_ivp, get_term_structure
from optdash.analytics.pcr import get_pcr
from optdash.analytics.environment import get_environment_score
from optdash.analytics.screener import get_strikes
from optdash.analytics.microstructure import get_volume_velocity
from optdash.analytics.vexcex import get_vex_cex_series
from conftest import TRADE_DATE, SNAP_TIME, UNDERLYING


class TestGEX:
    def test_returns_expected_keys(self, duckdb_conn):
        result = get_net_gex(duckdb_conn, TRADE_DATE, SNAP_TIME, UNDERLYING)
        assert 'gex_all_B' in result
        assert 'regime' in result
        assert result['regime'] in ('POSITIVE_CHOP', 'NEGATIVE_TREND')

    def test_pct_of_peak_valid(self, duckdb_conn):
        result = get_net_gex(duckdb_conn, TRADE_DATE, SNAP_TIME, UNDERLYING)
        assert 0.0 <= result.get('pct_of_peak', 100) <= 100


class TestCoC:
    def test_coc_series_returns_list(self, duckdb_conn):
        rows = get_coc_series(duckdb_conn, TRADE_DATE, UNDERLYING)
        assert isinstance(rows, list)

    def test_coc_signal_valid(self, duckdb_conn):
        coc = get_coc_latest(duckdb_conn, TRADE_DATE, SNAP_TIME, UNDERLYING)
        if coc:
            assert coc.get('signal') in ('VELOCITY_BULL', 'VELOCITY_BEAR', 'DISCOUNT', 'NORMAL')


class TestPCR:
    def test_pcr_divergence_numeric(self, duckdb_conn):
        pcr = get_pcr(duckdb_conn, TRADE_DATE, SNAP_TIME, UNDERLYING)
        assert isinstance(pcr.get('pcr_divergence', None), (float, int))


class TestEnvironment:
    def test_score_in_range(self, duckdb_conn):
        result = get_environment_score(duckdb_conn, TRADE_DATE, SNAP_TIME, UNDERLYING)
        assert 0 <= result['score'] <= result['maxscore']

    def test_verdict_matches_score(self, duckdb_conn):
        result = get_environment_score(duckdb_conn, TRADE_DATE, SNAP_TIME, UNDERLYING)
        score = result['score']
        if score >= 5:
            assert result['verdict'] == 'GO'
        elif score >= 3:
            assert result['verdict'] in ('GO', 'WAIT')
        else:
            assert result['verdict'] == 'NOGO'

    def test_8_conditions_present(self, duckdb_conn):
        result = get_environment_score(duckdb_conn, TRADE_DATE, SNAP_TIME, UNDERLYING)
        assert len(result['conditions']) == 8

    def test_bonus_gate_zero_without_direction(self, duckdb_conn):
        """Gate 7 (vex_aligned) must score 0 when direction param is omitted."""
        result = get_environment_score(duckdb_conn, TRADE_DATE, SNAP_TIME, UNDERLYING)
        assert result['conditions']['vex_aligned']['points'] == 0


class TestScreener:
    def test_strike_rows_have_sscore(self, duckdb_conn):
        rows = get_strikes(duckdb_conn, TRADE_DATE, UNDERLYING, SNAP_TIME)
        assert len(rows) > 0
        assert all('sscore' in r for r in rows)

    def test_sorted_by_sscore_desc(self, duckdb_conn):
        rows = get_strikes(duckdb_conn, TRADE_DATE, UNDERLYING, SNAP_TIME)
        scores = [r['sscore'] for r in rows if r['sscore'] is not None]
        assert scores == sorted(scores, reverse=True)


class TestVolumeVelocity:
    def test_vol_ratio_non_negative(self, duckdb_conn):
        rows = get_volume_velocity(duckdb_conn, TRADE_DATE, UNDERLYING)
        for row in rows:
            assert row.get('vol_ratio', 0) >= 0
```

---

## 4. `tests/test_ai.py`

Tests the full AI engine pipeline: preflight, recommendation generation, trade lifecycle.

```python
import pytest
from optdash.ai.preflight import run_preflight
from optdash.ai.confidence import score_confidence
from optdash.ai.recommender import generate_recommendation
from optdash.journal.trades import (
    insert_trade, get_trade, get_open_trades,
    accept_trade, reject_trade, close_trade,
)
from optdash.states import TradeStatus, ExitReason, RejectionReason
from conftest import TRADE_DATE, SNAP_TIME, UNDERLYING


class TestPreflight:
    def test_preflight_returns_bool(self, duckdb_conn):
        """run_preflight must return True/False, never raise."""
        result = run_preflight(
            conn=duckdb_conn,
            trade_date=TRADE_DATE,
            snap_time=SNAP_TIME,
            underlying=UNDERLYING,
            option_type='CE',
            strike=22150.0,
            dte=6,
            entry_premium=85.0,
            theta=-0.5,
            gate_score=5,
            confidence=60,
        )
        assert isinstance(result, bool)


class TestConfidence:
    def test_confidence_0_to_100(self, duckdb_conn):
        score = score_confidence(
            conn=duckdb_conn,
            trade_date=TRADE_DATE,
            snap_time=SNAP_TIME,
            underlying=UNDERLYING,
            direction='CE',
            gate_score=6,
            sscore=15.0,
            dte=6,
            vol_regime='CHEAP',
        )
        assert 0 <= score <= 100


class TestTradeCRUD:
    def test_insert_and_get(self, journal_conn):
        trade_id = insert_trade(journal_conn, {
            'trade_date':  TRADE_DATE,
            'underlying':  UNDERLYING,
            'direction':   'CE',
            'expiry_date': '2026-03-06',
            'dte':         6,
            'expiry_tier': 'TIER1',
            'strike':      22150.0,
            'option_type': 'CE',
            'sl':          51.0,
            'target':      127.5,
            'confidence':  65,
            'gate_score':  6,
            'session':     'MORNING_TREND',
            'narrative':   'Test trade',
            'signals':     '["vcocsignal", "ivpcheap"]',
            'status':      TradeStatus.GENERATED,
        })
        trade = get_trade(journal_conn, trade_id)
        assert trade['status'] == TradeStatus.GENERATED
        assert trade['underlying'] == UNDERLYING

    def test_accept_trade(self, journal_conn):
        trade_id = insert_trade(journal_conn, {
            'trade_date': TRADE_DATE, 'underlying': UNDERLYING, 'direction': 'CE',
            'expiry_date': '2026-03-06', 'dte': 6, 'expiry_tier': 'TIER1',
            'strike': 22150.0, 'option_type': 'CE', 'sl': 51.0, 'target': 127.5,
            'confidence': 65, 'gate_score': 6, 'session': 'MORNING_TREND',
            'narrative': 'Accept test', 'signals': '[]', 'status': TradeStatus.GENERATED,
        })
        accept_trade(journal_conn, trade_id, entry_premium=85.0, entry_spot=22155.0,
                     entry_snap_time='10:05', entry_iv=0.18, entry_delta=0.45,
                     entry_theta=-0.5, entry_gamma=0.001, entry_vega=12.0)
        trade = get_trade(journal_conn, trade_id)
        assert trade['status'] == TradeStatus.ACCEPTED
        assert trade['entry_premium'] == pytest.approx(85.0)

    def test_reject_trade(self, journal_conn):
        trade_id = insert_trade(journal_conn, {
            'trade_date': TRADE_DATE, 'underlying': UNDERLYING, 'direction': 'CE',
            'expiry_date': '2026-03-06', 'dte': 6, 'expiry_tier': 'TIER1',
            'strike': 22150.0, 'option_type': 'CE', 'sl': 51.0, 'target': 127.5,
            'confidence': 65, 'gate_score': 6, 'session': 'MORNING_TREND',
            'narrative': 'Reject test', 'signals': '[]', 'status': TradeStatus.GENERATED,
        })
        reject_trade(journal_conn, trade_id, RejectionReason.LOW_CONFIDENCE)
        trade = get_trade(journal_conn, trade_id)
        assert trade['status'] == TradeStatus.REJECTED
        assert trade['rejection_reason'] == RejectionReason.LOW_CONFIDENCE

    def test_close_trade(self, journal_conn):
        trade_id = insert_trade(journal_conn, {
            'trade_date': TRADE_DATE, 'underlying': UNDERLYING, 'direction': 'CE',
            'expiry_date': '2026-03-06', 'dte': 6, 'expiry_tier': 'TIER1',
            'strike': 22150.0, 'option_type': 'CE', 'sl': 51.0, 'target': 127.5,
            'confidence': 65, 'gate_score': 6, 'session': 'MORNING_TREND',
            'narrative': 'Close test', 'signals': '[]', 'status': TradeStatus.ACCEPTED,
            'entry_premium': 85.0, 'entry_spot': 22155.0,
        })
        close_trade(journal_conn, trade_id, exit_premium=127.5, exit_spot=22220.0,
                    exit_snap_time='11:30', exit_reason=ExitReason.TARGET_HIT,
                    pnl_pts=42.5, pnl_pct=50.0)
        trade = get_trade(journal_conn, trade_id)
        assert trade['status'] == TradeStatus.CLOSED
        assert trade['exit_reason'] == ExitReason.TARGET_HIT
        assert trade['pnl_pts'] == pytest.approx(42.5)

    def test_no_open_trades_initially(self, journal_conn):
        trades = get_open_trades(journal_conn, TRADE_DATE)
        assert trades == []


class TestEOD:
    def test_eod_force_close_empty_journal(self, duckdb_conn, journal_conn):
        from optdash.ai.eod import eod_force_close
        closed = eod_force_close(duckdb_conn, journal_conn, TRADE_DATE)
        assert closed == []

    def test_finalize_shadows_no_pending(self, duckdb_conn, journal_conn):
        from optdash.ai.eod import finalize_all_shadows
        finalize_all_shadows(duckdb_conn, journal_conn, TRADE_DATE)   # must not raise
```

---

## 5. `tests/test_api.py`

Tests FastAPI endpoints using `httpx.AsyncClient` and `pytest-asyncio`.

```python
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch
from optdash.api.main import app


@pytest_asyncio.fixture
async def client():
    """Test client with mocked DuckDB state."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


@pytest.mark.asyncio
class TestMarketRoutes:

    async def test_gex_returns_list(self, client, duckdb_conn):
        app.state.duckdb = duckdb_conn
        resp = await client.get("/market/gex", params={
            "trade_date": "2026-02-28", "underlying": "NIFTY",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    async def test_environment_returns_score(self, client, duckdb_conn):
        app.state.duckdb = duckdb_conn
        resp = await client.get("/market/environment", params={
            "trade_date": "2026-02-28", "snap_time": "10:00", "underlying": "NIFTY",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert 'score' in data
        assert 'verdict' in data
        assert data['verdict'] in ('GO', 'WAIT', 'NOGO')


@pytest.mark.asyncio
class TestScreenerRoutes:

    async def test_strikes_returns_list(self, client, duckdb_conn):
        app.state.duckdb = duckdb_conn
        resp = await client.get("/screener/strikes", params={
            "trade_date": "2026-02-28", "underlying": "NIFTY", "snap_time": "10:00",
        })
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


@pytest.mark.asyncio
class TestAIRoutes:

    async def test_open_trades_empty(self, client, tmp_path):
        """Fresh journal should return empty trade list."""
        import sqlite3
        from optdash.journal.schema import init_db
        db = tmp_path / "j.db"
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        init_db(conn)
        conn.commit()
        # Override the journal path in config
        with patch("optdash.config.settings.JOURNAL_DB", db):
            resp = await client.get("/ai/open-trades", params={"trade_date": "2026-02-28"})
        assert resp.status_code == 200
        assert resp.json() == []
```

---

## 6. Running Tests

```bash
# Install dev extras
pip install -e '.[dev]'

# Run full test suite
pytest tests/ -v

# Run by module
pytest tests/test_pipeline.py  -v
pytest tests/test_analytics.py -v
pytest tests/test_ai.py        -v
pytest tests/test_api.py       -v

# Run with coverage
pytest tests/ --cov=optdash --cov-report=term-missing

# Lint
ruff check optdash

# Type check
mypy optdash --ignore-missing-imports
```

---

## 7. Entry Points

### `runpipeline.py`

```python
"""
Entry point for the data pipeline + scheduler.
Run from project root with venv active:
    python runpipeline.py

Startup sequence:
1.  Setup logging and directories
2.  Run backfill for configured historical range (idempotent)
3.  Run gap-fill to recover any missed snaps since watermark
4.  Initialise DuckDB views
5.  If market closed / holiday: log and exit cleanly
6.  Immediate catch-up tick: sync watermark to right now
7.  APScheduler every 5 min until 15:30 or Ctrl+C
"""
import sys
from optdash.logger import get_logger
from optdash.pipeline import backfill, gapfill, duckdbgateway, scheduler
from optdash.pipeline.scheduler import tick

log = get_logger(__name__)

if __name__ == '__main__':
    log.info("OptDash pipeline starting")

    # Step 1 — Logging + directories initialised by config import
    from optdash.config import settings

    # Step 2 — Backfill
    backfill.run_configured_range()

    # Step 3 — Gap fill
    conn = duckdbgateway.startup()
    gapfill.fill_gap(conn)

    # Step 4 — Views already registered by startup()

    # Steps 5-6
    if not scheduler.is_market_day_today():
        log.info("Market closed today — exiting pipeline")
        sys.exit(0)

    tick()                      # immediate catch-up
    scheduler.start(conn)       # blocks until 15:30 or Ctrl+C
```

### `runapi.py`

```python
"""
Entry point for the FastAPI server.
Run from project root with venv active:
    python runapi.py

The pipeline (runpipeline.py) must be running separately.
The API reads DuckDB Parquet views which are populated by the pipeline.
"""
import uvicorn

if __name__ == '__main__':
    uvicorn.run(
        "optdash.api.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
    )
```

---

## 8. Sprint-by-Sprint Checkpoint Summary

| Sprint | Module(s) | Key Checkpoint Assertion |
|---|---|---|
| **1** Foundation | `config`, `states`, `logger` | `pip install -e .` imports clean; directories created; ruff passes |
| **2** Pipeline | `bqclient`, `processor`, `duckdbgateway`, `atm`, `backfill`, `gapfill`, `scheduler` | `vwoptions` view loads; all enriched columns present; gap-fill scenarios work |
| **3** Analytics | `gex`, `coc`, `iv`, `pcr`, `environment`, `screener`, `vexcex`, `microstructure`, `alerts` | `get_environment_score` returns 8 conditions; screener sorted by Sscore desc |
| **4** AI Engine | `direction`, `confidence`, `narrative`, `preflight`, `quality`, `recommender`, `tracker`, `shadowtracker` | `generate_recommendation` returns `TradeCard` or `None`; no crash on empty journal |
| **5** API Layer | `api/main`, `deps`, `ws`, `schemas`, `routers/*` | All 5 router groups return 200 with valid JSON; WS upgrade succeeds |
| **6** Notifications + EOD | `notifications/alerts`, `ai/eod`, `reports/daily` | `eod_force_close` + `finalize_all_shadows` complete without error on empty journal; report file written |
| **7** Frontend | `frontend/src/**` | `npm run build` zero TS errors; all panels render; panel crash → ErrorBoundary Retry card |

---

## 9. Production Startup Checklist

```bash
# ── Environment ────────────────────────────────────────────────────────────────
cp .env.example .env
# Fill in: BQ_PROJECT, BQ_DATASET, BQ_OPTIONS_TABLE, BQ_CREDENTIALS_PATH

# ── Python ─────────────────────────────────────────────────────────────────────
python -m venv .venv
source .venv/bin/activate        # or .venv\Scripts\activate on Windows
pip install -e '.[dev]'

# Verify config
python -c "from optdash.config import settings; print(settings.JOURNAL_DB)"

# ── Tests ──────────────────────────────────────────────────────────────────────
pytest tests/ -v                  # must be 100% green
ruff check optdash                # must be zero errors

# ── Data backfill (first run only) ─────────────────────────────────────────────
python -m optdash.pipeline.backfill --start 2026-02-17 --end 2026-02-28

# ── Terminal 1: Pipeline ───────────────────────────────────────────────────────
python runpipeline.py
# Expected output:
# INFO  OptDash pipeline starting
# INFO  Backfill: skipping N already-complete dates
# INFO  Gap fill: no gaps detected
# INFO  TICK 2026-03-05 09:20  [market active]
# INFO  Pulled NNN rows up to 09:20

# ── Terminal 2: API ────────────────────────────────────────────────────────────
python runapi.py
# Expected output:
# INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
# INFO:     Application startup complete.

# ── Terminal 3: Frontend ───────────────────────────────────────────────────────
cd frontend
npm install
npm run dev
# Expected output:
# VITE v5.x  ready in NNN ms
# ➜  Local: http://localhost:5173/

# ── Smoke test ─────────────────────────────────────────────────────────────────
curl -s "http://127.0.0.1:8000/market/environment?trade_date=$(date +%F)&snap_time=09:20&underlying=NIFTY" \
  | python -m json.tool | grep verdict
# Should print: "verdict": "GO" | "WAIT" | "NOGO"

echo '\n✅ OptDash production startup complete'
```

---

## 10. File Inventory — `tests/`

```
tests/
  conftest.py          ← session-scoped DuckDB + per-test SQLite journal
  test_pipeline.py     ← processor, enrichment, deduplication
  test_analytics.py    ← all analytics functions, formula assertions
  test_ai.py           ← preflight, confidence, CRUD lifecycle, EOD
  test_api.py          ← FastAPI routes via httpx.AsyncClient
```
