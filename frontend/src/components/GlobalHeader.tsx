import { useDashboardStore } from '../store/dashboardStore'
import { useSpot } from '../hooks/useMarket'
import clsx from 'clsx'

const UNDERLYINGS = ['NIFTY', 'BANKNIFTY', 'FINNIFTY']

export default function GlobalHeader() {
  const { underlying, setUnderlying, tradeDate, setTradeDate, snapMode, setSnapMode } = useDashboardStore()
  const { data: spot } = useSpot()

  const pnlClass = spot?.change_pct != null
    ? (spot.change_pct >= 0 ? 'val-bull' : 'val-bear')
    : 'val-neutral'

  return (
    <header className="bg-bg-surface border-b border-border-dim h-11 flex items-center px-4 gap-4 shrink-0">
      <span className="text-brand font-semibold text-sm tracking-wider">OptDash</span>

      {/* Underlying selector */}
      <div className="flex gap-1">
        {UNDERLYINGS.map(u => (
          <button
            key={u}
            onClick={() => setUnderlying(u)}
            className={clsx(
              'text-xs px-2 py-1 rounded transition-colors',
              underlying === u
                ? 'bg-brand text-white'
                : 'text-muted hover:text-gray-200 hover:bg-white/5'
            )}
          >
            {u}
          </button>
        ))}
      </div>

      {/* Spot ticker */}
      {spot && (
        <div className="flex items-center gap-2 text-sm font-mono">
          <span className="text-gray-400">{spot.spot.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span>
          <span className={pnlClass}>{spot.change_pct >= 0 ? '+' : ''}{spot.change_pct.toFixed(2)}%</span>
        </div>
      )}

      <div className="ml-auto flex items-center gap-3">
        {/* Trade date */}
        <input
          type="date"
          value={tradeDate}
          onChange={e => setTradeDate(e.target.value)}
          className="bg-bg-panel border border-border-dim rounded px-2 py-1 text-xs text-gray-300"
        />
        {/* Live / Replay toggle */}
        <button
          onClick={() => setSnapMode(snapMode === 'LIVE' ? 'REPLAY' : 'LIVE')}
          className={clsx(
            'text-xs px-2 py-1 rounded font-semibold transition-colors',
            snapMode === 'LIVE'
              ? 'bg-bull/20 text-bull-light'
              : 'bg-accent/20 text-accent'
          )}
        >
          {snapMode === 'LIVE' ? '● LIVE' : '⏸ REPLAY'}
        </button>
      </div>
    </header>
  )
}
