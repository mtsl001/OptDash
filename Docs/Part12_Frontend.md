# OptDash — Part 12: Frontend

The frontend is a Vite + React 18 + TypeScript single-page application. It polls the FastAPI backend on a per-panel cadence, renders a dark trading-terminal UI, and supports full historical scrubbing via a snap-time slider.

---

## 1. Tech Stack

| Layer | Package | Version |
|---|---|---|
| Build | Vite | ^5.0 |
| Framework | React | ^18.3 |
| Language | TypeScript | ^5.4 |
| Charts | Recharts | ^2.12 |
| Data fetching | TanStack Query v5 | ^5.28 |
| HTTP client | Axios | ^1.6 |
| State | Zustand | ^4.5 |
| Styling | Tailwind CSS | ^3.4 |

---

## 2. File Structure

```
frontend/
  package.json
  vite.config.ts
  tsconfig.json
  tailwind.config.ts
  src/
    main.tsx                  ← React root, QueryClientProvider, Zustand
    App.tsx                   ← ErrorBoundary wrapper, router
    api/
      client.ts               ← Axios instance (baseURL 127.0.0.1:8000)
      market.ts               ← spot, gex, coc, environment
      microstructure.ts       ← pcr, alerts, volumeVelocity, vexCex
      screener.ts             ← strikes, ivp, termStructure
      position.ts             ← thetaSlSeries, pnlAttribution
      ai.ts                   ← recommend, accept, reject, close, trades, learning
    store/
      useGlobalStore.ts       ← underlying, tradeDate, snapTime, isLive, soundOn
    hooks/
      useSpot.ts
      useGex.ts
      useCoC.ts
      useEnvironment.ts
      usePcr.ts
      useAlerts.ts
      useVolumeVelocity.ts
      useVexCex.ts
      useStrikes.ts
      useIvp.ts
      useTermStructure.ts
      useThetaSl.ts
      usePnlAttribution.ts
      useAiTrades.ts
      useLearning.ts
      useWs.ts                ← WebSocket hook for position events
    components/
      panels/
        EnvironmentGauge.tsx  ← Panel 1
        GEXPanel.tsx          ← Panel 2
        CoCVelocityPanel.tsx  ← Panel 3
        StrikeScreener.tsx    ← Panel 4
        PCRDivergencePanel.tsx← Panel 5
        AlertFeed.tsx         ← Panel 6
        VolumeVelocityPanel.tsx← Panel 7
        TermStructurePanel.tsx← Panel 8
        VannaCexPanel.tsx     ← Panel 9
        PositionMonitor.tsx   ← Panel 10
      ui/
        Badge.tsx
        Card.tsx
        Spinner.tsx
        ErrorBoundary.tsx
      layout/
        GlobalHeader.tsx
        Sidebar.tsx
    pages/
      Dashboard.tsx
      Screener.tsx
      Journal.tsx
      Learning.tsx
    types/
      market.ts
      screener.ts
      microstructure.ts
      position.ts
      ai.ts
```

---

## 3. Colour Palette — Dark Trading Terminal

All tokens are defined in `tailwind.config.ts` and used as Tailwind utility classes.

| Token | Hex | Used For |
|---|---|---|
| `bg-panel` | `#0F1923` | Page background |
| `bg-surface` | `#162030` | Card / panel background |
| `border` | `#243040` | All borders, chart gridlines |
| `brand-light` | `#2E75B6` | CEs, neutral signals, brand accents |
| `accent` | `#E8A020` | Highlights, warnings, far-expiry, Dealer O'Clock |
| `bull` | `#1E7C44` | Positive, GO, bullish |
| `bear` | `#C0392B` | Negative, NOGO, bearish |
| `ink-muted` | `#808080` | Secondary labels, axis ticks |

```ts
// tailwind.config.ts
export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        "bg-panel":   "#0F1923",
        "bg-surface": "#162030",
        "border":     "#243040",
        "brand-light":"#2E75B6",
        "accent":     "#E8A020",
        "bull":       "#1E7C44",
        "bear":       "#C0392B",
        "ink-muted":  "#808080",
      },
    },
  },
}
```

---

## 4. `vite.config.ts`

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/market':   'http://127.0.0.1:8000',
      '/micro':    'http://127.0.0.1:8000',
      '/screener': 'http://127.0.0.1:8000',
      '/position': 'http://127.0.0.1:8000',
      '/ai':       'http://127.0.0.1:8000',
    },
  },
})
```

The Vite proxy means the frontend uses relative URLs (`/market/gex`) in development; CORS middleware in FastAPI covers production builds.

---

## 5. TypeScript Interfaces — `src/types/`

### `types/market.ts`

```ts
export interface SpotData {
  snap_time: string
  spot: number
  day_open: number
  day_high: number
  day_low: number
  change_pct: number
}

export interface GEXRow {
  snap_time:   string
  gex_all_B:   number
  gex_near_B:  number
  gex_far_B:   number
  pct_of_peak: number
  regime:      'POSITIVE_CHOP' | 'NEGATIVE_TREND'
}

export interface CoCRow {
  snap_time: string
  fut_price: number
  spot:      number
  coc:       number
  vcoc_15m:  number
  signal:    'VELOCITY_BULL' | 'VELOCITY_BEAR' | 'DISCOUNT' | 'NORMAL'
}

export interface ConditionDetail {
  met:       boolean
  value:     number | string | null
  threshold: string
  points:    number
  note:      string
  is_bonus?: boolean
}

export interface EnvironmentScore {
  score:      number
  maxscore:   number
  verdict:    'GO' | 'WAIT' | 'NOGO'
  conditions: Record<string, ConditionDetail>
}
```

### `types/screener.ts`

```ts
export interface StrikeRow {
  expiry_date:  string
  dte:          number
  expiry_tier:  string
  option_type:  'CE' | 'PE'
  strike_price: number
  ltp:          number | null
  iv:           number | null
  delta:        number | null
  theta:        number | null
  gamma:        number | null
  vega:         number | null
  moneyn_pct:   number | null
  rho:          number | null
  eff_ratio:    number | null
  sscore:       number | null
  stars:        0 | 1 | 2 | 3 | 4
}

export interface IVPResponse {
  underlying:    string
  snap_time:     string
  ivp:           number
  ivr:           number
  atm_iv:        number
  hv20:          number
  iv_hv_spread:  number
  vol_regime:    'RICH' | 'FAIR' | 'CHEAP'
}

export interface TermStructureRow {
  expiry_date: string
  dte:         number
  expiry_tier: 'TIER1' | 'TIER2' | 'TIER3'
  atm_iv:      number
  avg_theta:   number
  shape:       'CONTANGO' | 'FLAT' | 'BACKWARDATION'
}
```

### `types/microstructure.ts`

```ts
export interface PCRRow {
  snap_time:      string
  pcr_vol:        number
  pcr_oi:         number
  pcr_divergence: number
  smoothed_obi:   number
  signal:         'RETAIL_PANIC_PUTS' | 'DIVERGENCE_BUILDING' | 'RETAIL_PANIC_CALLS' | 'BALANCED'
}

export interface AlertItem {
  time:      string
  type:      'COC_VELOCITY' | 'GEX_DECLINE' | 'PCR_DIVERGENCE' | 'OBI_SHIFT' | 'VOLUME_SPIKE' | 'GATE_CHANGE'
  severity:  'HIGH' | 'MEDIUM' | 'LOW'
  direction: string | null
  headline:  string
  message:   string
}

export interface VolumeVelocityRow {
  snap_time:    string
  vol_total:    number
  baseline_vol: number
  vol_ratio:    number
  signal:       'EXTREME_SURGE' | 'SPIKE' | 'ELEVATED' | 'NORMAL'
}

export interface VexCexSeriesRow {
  snap_time:      string
  vex_total_M:    number
  vex_ce_M:       number
  vex_pe_M:       number
  cex_total_M:    number
  cex_ce_M:       number
  cex_pe_M:       number
  spot:           number
  dte:            number
  vex_signal:     string
  cex_signal:     string
  dealer_oclock:  boolean
  interpretation: string
}

export interface VexCexStrikeRow {
  strike_price: number
  option_type:  'CE' | 'PE'
  moneyn_pct:   number
  vex_M:        number
  cex_M:        number
  oi:           number
  iv:           number
  dte:          number
}

export interface VexCexResponse {
  series:         VexCexSeriesRow[]
  by_strike:      VexCexStrikeRow[]
  current:        VexCexSeriesRow | null
  dealer_oclock:  boolean
  interpretation: string
}
```

### `types/position.ts`

```ts
export interface ThetaSLPoint {
  snap_time:      string
  entry_premium:  number
  theta_daily:    number
  sl_base:        number
  sl_adjusted:    number
  current_ltp:    number
  unrealised_pnl: number
  pnl_pct:        number
  status: 'IN_TRADE' | 'STOP_HIT' | 'PROFIT_ZONE_PARTIAL_EXIT' | 'GUARANTEED_PROFIT_ZONE'
}

export interface PnLAttributionRow {
  snap_time:       string
  ltp:             number
  spot:            number
  delta_pnl:       number
  gamma_pnl:       number
  vega_pnl:        number
  theta_pnl:       number
  actual_pnl:      number
  theoretical_pnl: number
  unexplained:     number
}
```

### `types/ai.ts`

```ts
export interface TradeCard {
  id:               number
  trade_date:       string
  created_at:       string
  underlying:       string
  direction:        'CE' | 'PE'
  expiry_date:      string
  dte:              number
  expiry_tier:      string
  strike:           number
  option_type:      'CE' | 'PE'
  entry_premium:    number | null
  entry_spot:       number | null
  sl:               number
  target:           number
  status:           'GENERATED' | 'ACCEPTED' | 'REJECTED' | 'EXPIRED' | 'CLOSED'
  exit_reason:      string | null
  rejection_reason: string | null
  shadow_outcome:   string | null
  pnl_pts:          number | null
  pnl_pct:          number | null
  confidence:       number
  gate_score:       number
  session:          string
  narrative:        string
  signals:          string[]
}
```

---

## 6. Zustand Global Store — `src/store/useGlobalStore.ts`

```ts
import { create } from 'zustand'

interface GlobalState {
  underlying:  string
  tradeDate:   string           // 'YYYY-MM-DD', today by default
  snapTime:    string           // 'HH:MM', empty = LIVE
  isLive:      boolean          // snapTime === '' or latest snap
  soundOn:     boolean

  setUnderlying: (u: string)  => void
  setTradeDate:  (d: string)  => void
  setSnapTime:   (t: string)  => void
  toggleSound:   ()           => void
}

export const useGlobalStore = create<GlobalState>((set) => ({
  underlying: 'NIFTY',
  tradeDate:  new Date().toISOString().slice(0, 10),
  snapTime:   '',           // '' = LIVE mode
  isLive:     true,
  soundOn:    false,

  setUnderlying: (u) => set({ underlying: u }),
  setTradeDate:  (d) => set({ tradeDate: d }),
  setSnapTime:   (t) => set({ snapTime: t, isLive: t === '' }),
  toggleSound:   ()  => set((s) => ({ soundOn: !s.soundOn })),
}))
```

---

## 7. API Client — `src/api/client.ts`

```ts
import axios from 'axios'

export const api = axios.create({
  baseURL: '/',                    // Vite proxy handles /market, /micro, etc.
  timeout: 10_000,
  headers: { 'Content-Type': 'application/json' },
})
```

Each router file calls `api.get(...)` or `api.post(...)` and returns the typed response directly. Example (`src/api/market.ts`):

```ts
import { api } from './client'
import type { GEXRow, CoCRow, EnvironmentScore, SpotData } from '../types/market'

export const fetchSpot = (tradeDate: string, underlying: string) =>
  api.get<SpotData[]>('/market/spot', { params: { trade_date: tradeDate, underlying } })
    .then(r => r.data)

export const fetchGex = (tradeDate: string, underlying: string) =>
  api.get<GEXRow[]>('/market/gex', { params: { trade_date: tradeDate, underlying } })
    .then(r => r.data)

export const fetchCoC = (tradeDate: string, underlying: string) =>
  api.get<CoCRow[]>('/market/coc', { params: { trade_date: tradeDate, underlying } })
    .then(r => r.data)

export const fetchEnvironment = (
  tradeDate: string, snapTime: string, underlying: string, direction?: string,
) =>
  api.get<EnvironmentScore>('/market/environment', {
    params: { trade_date: tradeDate, snap_time: snapTime, underlying, direction },
  }).then(r => r.data)
```

---

## 8. TanStack Query Hooks — `src/hooks/`

All hooks follow the same pattern: read global store → call API fn → return `{ data, isLoading, isError }`.

```ts
// hooks/useGex.ts
import { useQuery } from '@tanstack/react-query'
import { useGlobalStore } from '../store/useGlobalStore'
import { fetchGex } from '../api/market'

export function useGex() {
  const { underlying, tradeDate } = useGlobalStore()
  return useQuery({
    queryKey:  ['gex', underlying, tradeDate],
    queryFn:   () => fetchGex(tradeDate, underlying),
    refetchInterval: 5_000,
    staleTime:       4_000,
  })
}
```

### Polling Intervals

| Hook(s) | `refetchInterval` | Reason |
|---|---|---|
| `useSpot`, `useGex`, `useCoC`, `useEnvironment`, `usePcr`, `useAlerts`, `useVolumeVelocity`, `useVexCex` | **5 000 ms** | Live signals |
| `useStrikes`, `useTermStructure` | **30 000 ms** | Slower-moving |
| `useThetaSl`, `usePnlAttribution` | **disabled** | User-triggered |
| `useAiTrades` | **10 000 ms** | Trade state changes |

---

## 9. Panel Components

### Panel 1 — `EnvironmentGauge.tsx`

- **API**: `GET /market/environment`
- **Props**: none (reads store)
- **Display**:
  - Large score number, colour-coded: `bull` = GO, `accent` = WAIT, `bear` = NOGO
  - Progress bar from 0 to `maxscore` (11)
  - One row per condition: label, live value, threshold, tick/cross badge
  - Contextual notes at bottom (VCoC note, PCR note)
  - Bonus conditions (7 & 8) rendered with `⭐ Bonus` pill

### Panel 2 — `GEXPanel.tsx`

- **API**: `GET /market/gex`
- **Chart**: Recharts `ComposedChart` — Near GEX bars (`brand-light`), Far GEX bars (`accent`), `% of Peak` line (`bear`)
- **Reference line**: dashed at 70 threshold
- **Alert banner**: red strip when `pct_of_peak < 70`
- **KPIs**: GEX total, % of Peak, regime badge

### Panel 3 — `CoCVelocityPanel.tsx`

- **API**: `GET /market/coc`
- **Chart**: `ComposedChart` — CoC bars (`brand-light`, left axis), VCoC 15m line (`accent`, right axis)
- **Reference lines**: dashed at ±10 (VELOCITY_BULL / VELOCITY_BEAR)
- **Signal badge**: VELOCITY_BULL = bull, VELOCITY_BEAR = bear, DISCOUNT = accent, NORMAL = muted

### Panel 4 — `StrikeScreener.tsx`

- **API**: `GET /screener/strikes?top_n=20`
- **Display**: Sortable table
- **Columns**: Expiry | DTE | Type | Strike | LTP | IV | Delta | EffRatio | Rho | Sscore | ★★★★
- **Stars**: ≥20 = ★★★★, ≥10 = ★★★, ≥5 = ★★, else ★
- **Row colour**: CE rows tinted `brand-light/10`, PE rows tinted `bear/10`

### Panel 5 — `PCRDivergencePanel.tsx`

- **API**: `GET /micro/pcr`
- **Chart**: `ComposedChart` — PCR Vol bars, PCR OI line, PCR Divergence area
- **Signal pill**: RETAIL_PANIC_PUTS = bear, DIVERGENCE_BUILDING = accent, RETAIL_PANIC_CALLS = bull, BALANCED = muted

### Panel 6 — `AlertFeed.tsx`

- **API**: `GET /micro/alerts`
- **Display**: Scrollable feed, newest first
- **Row layout**: `[time] [severity badge] [type icon] [headline]`
- **Severity colours**: HIGH = bear, MEDIUM = accent, LOW = ink-muted
- **Sound**: Web Audio API beep on new HIGH alert when `soundOn === true`

```ts
// Sound trigger inside AlertFeed.tsx
const { soundOn } = useGlobalStore()
const prevCount = useRef(0)

useEffect(() => {
  const newHighs = data?.filter(a => a.severity === 'HIGH') ?? []
  if (soundOn && newHighs.length > prevCount.current) {
    const ctx = new AudioContext()
    const osc = ctx.createOscillator()
    osc.connect(ctx.destination)
    osc.frequency.value = 880
    osc.start()
    osc.stop(ctx.currentTime + 0.12)
  }
  prevCount.current = newHighs.length
}, [data, soundOn])
```

### Panel 7 — `VolumeVelocityPanel.tsx`

- **API**: `GET /micro/volume-velocity`
- **Chart**: `BarChart` — bars colour-coded by `vol_ratio`:
  - ≥3.0 → `bear`, ≥2.0 → `accent`, ≥1.5 → `brand-light/60`, else `brand-light/30`
- **Reference line**: dashed at 2.0 (spike threshold)
- **KPI**: current ratio, baseline, signal badge

### Panel 8 — `TermStructurePanel.tsx`

- **API**: `GET /screener/term-structure`
- **Chart**: `LineChart` — ATM IV vs DTE for each expiry
- **Colours**: CONTANGO = bull, FLAT = accent, BACKWARDATION = bear
- **Shape badge**: shown in panel header
- **Theta row**: avg theta per expiry shown below chart

### Panel 9 — `VannaCexPanel.tsx`

- **API**: `GET /micro/vex-cex`
- **Chart 1** (VEX): Stacked `BarChart` — VEX CE (`brand-light`), VEX PE (`bear`), net VEX line (white)
- **Chart 2** (CEX): `BarChart` — bars colour-coded by intensity (green = bullish, red = pressure)
- **Reference lines**: dashed at ±20 M
- **KPI row**: VEX total, VEX signal badge, CEX total, CEX signal badge, DTE
- **Dealer O'Clock badge**: orange `accent` banner when `dealer_oclock === true`
- **Interpretation footer**: `current.interpretation` text in `ink-muted`
- **No-data state**: "No VEX/CEX data — Parquet files predate VEX/CEX columns" placeholder

### Panel 10 — `PositionMonitor.tsx`

- **APIs**: `GET /position/theta-sl-series`, `GET /position/pnl-attribution`
- **Trigger**: user selects an open trade from the AI trade list
- **Chart 1** (Theta SL): Line chart — `current_ltp` (white), `sl_adjusted` (bear), `sl_base` (muted)
  - Status zone shading: GUARANTEED_PROFIT_ZONE = bull/10 background
- **Chart 2** (PnL Attribution): Stacked `BarChart` per snap — delta PnL (brand-light), gamma (accent), vega (bull), theta (bear), unexplained (ink-muted)
- **Status badge**: IN_TRADE / STOP_HIT / PROFIT_ZONE / GUARANTEED_PROFIT_ZONE

---

## 10. Shared UI Components

### `ui/Card.tsx`

```tsx
export function Card({ title, children, className = '' }: CardProps) {
  return (
    <div className={`bg-bg-surface border border-border rounded-lg p-4 ${className}`}>
      {title && (
        <h3 className="text-xs font-semibold text-ink-muted uppercase tracking-wider mb-3">
          {title}
        </h3>
      )}
      {children}
    </div>
  )
}
```

### `ui/Badge.tsx`

```tsx
const VARIANT_CLASSES = {
  go:     'bg-bull/20 text-bull border-bull/30',
  wait:   'bg-accent/20 text-accent border-accent/30',
  nogo:   'bg-bear/20 text-bear border-bear/30',
  bull:   'bg-bull/20 text-bull border-bull/30',
  bear:   'bg-bear/20 text-bear border-bear/30',
  accent: 'bg-accent/20 text-accent border-accent/30',
  muted:  'bg-border/40 text-ink-muted border-border',
} as const

export function Badge({ label, variant }: BadgeProps) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded border text-xs font-medium
      ${VARIANT_CLASSES[variant]}`}>
      {label}
    </span>
  )
}
```

### `ui/Spinner.tsx`

```tsx
export function Spinner() {
  return (
    <div className="animate-spin h-5 w-5 border-2 border-brand-light border-t-transparent rounded-full" />
  )
}
```

### `ui/ErrorBoundary.tsx`

```tsx
import { Component, ErrorInfo, ReactNode } from 'react'

export class ErrorBoundary extends Component<
  { children: ReactNode },
  { hasError: boolean }
> {
  state = { hasError: false }

  static getDerivedStateFromError() { return { hasError: true } }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[Panel Error]', error, info)
  }

  render() {
    if (this.state.hasError)
      return (
        <div className="bg-bear/10 border border-bear/30 rounded-lg p-4 text-bear text-sm">
          Panel Error —{' '}
          <button className="underline" onClick={() => this.setState({ hasError: false })}>
            Retry
          </button>
        </div>
      )
    return this.props.children
  }
}
```

Each panel in `App.tsx` is wrapped:
```tsx
<ErrorBoundary><EnvironmentGauge /></ErrorBoundary>
```

---

## 11. `layout/GlobalHeader.tsx`

```tsx
// Controls:
// 1. Spot ticker  — live spot, change%, H/L
// 2. Underlying pill tabs — NIFTY | BANKNIFTY | FINNIFTY | MIDCPNIFTY | NIFTYNXT50
// 3. Date picker  — sets tradeDate in store, enables historical mode
// 4. Snap-time slider — 09:15 to 15:30 in 5-min steps; '' = LIVE (green pulse)
// 5. Sound toggle — 🔔 icon, orange when soundOn
```

The **snap-time slider** generates the range `['', '09:15', '09:20', ..., '15:30']`. Index 0 maps to LIVE mode (green animated pulse dot in header). When scrubbing, `setSnapTime` is called and all hooks re-query with the selected `snap_time`.

---

## 12. `pages/Dashboard.tsx`

```tsx
// 4-row grid layout:
// Row 1: EnvironmentGauge | GEXPanel | CoCVelocityPanel
// Row 2: StrikeScreener   | PCRDivergencePanel | AlertFeed
// Row 3: VolumeVelocityPanel | TermStructurePanel | VannaCexPanel
// Row 4: PositionMonitor (full-width)

export function Dashboard() {
  return (
    <div className="min-h-screen bg-bg-panel p-4 space-y-4">
      <GlobalHeader />
      <div className="grid grid-cols-3 gap-4">
        <ErrorBoundary><EnvironmentGauge /></ErrorBoundary>
        <ErrorBoundary><GEXPanel /></ErrorBoundary>
        <ErrorBoundary><CoCVelocityPanel /></ErrorBoundary>
      </div>
      <div className="grid grid-cols-3 gap-4">
        <ErrorBoundary><StrikeScreener /></ErrorBoundary>
        <ErrorBoundary><PCRDivergencePanel /></ErrorBoundary>
        <ErrorBoundary><AlertFeed /></ErrorBoundary>
      </div>
      <div className="grid grid-cols-3 gap-4">
        <ErrorBoundary><VolumeVelocityPanel /></ErrorBoundary>
        <ErrorBoundary><TermStructurePanel /></ErrorBoundary>
        <ErrorBoundary><VannaCexPanel /></ErrorBoundary>
      </div>
      <div className="grid grid-cols-1">
        <ErrorBoundary><PositionMonitor /></ErrorBoundary>
      </div>
    </div>
  )
}
```

---

## 13. WebSocket Hook — `hooks/useWs.ts`

```ts
import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'

export function useWs() {
  const qc = useQueryClient()
  const ws = useRef<WebSocket | null>(null)

  useEffect(() => {
    ws.current = new WebSocket('ws://127.0.0.1:8000/ai/ws')

    ws.current.onmessage = (e) => {
      const event = JSON.parse(e.data) as { type: string }
      if (['TRADE_GENERATED','TRADE_ACCEPTED','TRADE_REJECTED','TRADE_CLOSED'].includes(event.type)) {
        qc.invalidateQueries({ queryKey: ['ai-trades'] })
      }
      if (event.type === 'POSITION_SNAP') {
        qc.invalidateQueries({ queryKey: ['theta-sl'] })
        qc.invalidateQueries({ queryKey: ['pnl-attr'] })
      }
    }

    return () => ws.current?.close()
  }, [])
}
```

---

## 14. Sprint 7 — Files to Create (in order)

```
1.  frontend/package.json
2.  frontend/vite.config.ts
3.  frontend/tsconfig.json
4.  frontend/tailwind.config.ts
5.  frontend/src/main.tsx
6.  frontend/src/App.tsx
7.  frontend/src/api/client.ts
8.  frontend/src/api/market.ts
9.  frontend/src/api/microstructure.ts
10. frontend/src/api/screener.ts
11. frontend/src/api/position.ts
12. frontend/src/api/ai.ts
13. frontend/src/store/useGlobalStore.ts
14. frontend/src/types/market.ts
15. frontend/src/types/screener.ts
16. frontend/src/types/microstructure.ts
17. frontend/src/types/position.ts
18. frontend/src/types/ai.ts
19. frontend/src/hooks/useSpot.ts
20. frontend/src/hooks/useGex.ts
21. frontend/src/hooks/useCoC.ts
22. frontend/src/hooks/useEnvironment.ts
23. frontend/src/hooks/usePcr.ts
24. frontend/src/hooks/useAlerts.ts
25. frontend/src/hooks/useVolumeVelocity.ts
26. frontend/src/hooks/useVexCex.ts
27. frontend/src/hooks/useStrikes.ts
28. frontend/src/hooks/useIvp.ts
29. frontend/src/hooks/useTermStructure.ts
30. frontend/src/hooks/useThetaSl.ts
31. frontend/src/hooks/usePnlAttribution.ts
32. frontend/src/hooks/useAiTrades.ts
33. frontend/src/hooks/useWs.ts
34. frontend/src/components/ui/Badge.tsx
35. frontend/src/components/ui/Card.tsx
36. frontend/src/components/ui/Spinner.tsx
37. frontend/src/components/ui/ErrorBoundary.tsx
38. frontend/src/components/layout/GlobalHeader.tsx
39. frontend/src/components/layout/Sidebar.tsx
40. frontend/src/components/panels/EnvironmentGauge.tsx
41. frontend/src/components/panels/GEXPanel.tsx
42. frontend/src/components/panels/CoCVelocityPanel.tsx
43. frontend/src/components/panels/StrikeScreener.tsx
44. frontend/src/components/panels/PCRDivergencePanel.tsx
45. frontend/src/components/panels/AlertFeed.tsx
46. frontend/src/components/panels/VolumeVelocityPanel.tsx
47. frontend/src/components/panels/TermStructurePanel.tsx
48. frontend/src/components/panels/VannaCexPanel.tsx
49. frontend/src/components/panels/PositionMonitor.tsx
50. frontend/src/pages/Dashboard.tsx
51. frontend/src/pages/Screener.tsx
52. frontend/src/pages/Journal.tsx
53. frontend/src/pages/Learning.tsx
```

### Checkpoint

```bash
cd frontend
npm install
npm run build        # must complete with zero TypeScript errors
npm run dev          # http://localhost:5173

# With API running (python runapi.py):
# 1. Open http://localhost:5173
# 2. Switch underlying pill — all panels should reload
# 3. Move snap-time slider — panels should query historical data
# 4. Date picker to a past date — Environment gauge should show correct verdict
# 5. Kill the API — each panel should show Panel Error → Retry (not blank page)
echo 'SPRINT 7 FRONTEND CHECKPOINT PASSED'
```
