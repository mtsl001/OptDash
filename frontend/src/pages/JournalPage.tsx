import { useState } from 'react'
import { useTradeHistory } from '../hooks/useAI'
import clsx from 'clsx'

const STATUS_COLORS: Record<string, string> = {
  GENERATED: 'text-muted',
  ACCEPTED:  'text-brand',
  CLOSED:    'text-bull-light',
  REJECTED:  'text-bear-light',
  EXPIRED:   'text-muted',
}

export default function JournalPage() {
  const [page,   setPage]   = useState(1)
  const [status, setStatus] = useState<string | undefined>(undefined)
  const { data, isLoading } = useTradeHistory(page, 20, undefined, status)

  const statuses = ['', 'CLOSED', 'ACCEPTED', 'REJECTED', 'EXPIRED']

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <h1 className="text-sm font-semibold text-gray-200">Trade Journal</h1>
        <div className="flex gap-1">
          {statuses.map(s => (
            <button
              key={s}
              onClick={() => { setStatus(s || undefined); setPage(1) }}
              className={clsx(
                'text-xs px-2 py-1 rounded',
                (status ?? '') === s ? 'bg-brand text-white' : 'text-muted hover:text-gray-300'
              )}
            >
              {s || 'All'}
            </button>
          ))}
        </div>
      </div>

      <div className="panel">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted border-b border-border-dim">
              <th className="text-left pb-1">Date</th>
              <th className="text-left">Underlying</th>
              <th className="text-right">Strike</th>
              <th className="text-right">Entry</th>
              <th className="text-right">Exit</th>
              <th className="text-right">P&L%</th>
              <th className="text-right">Conf</th>
              <th className="text-right">Grade</th>
              <th className="text-right">Status</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={9} className="text-muted text-xs py-4 text-center">… loading</td></tr>
            )}
            {data?.trades?.map((t: any) => {
              const pnl = t.final_pnl_pct
              return (
                <tr key={t.id} className="border-b border-border-dim/40 hover:bg-white/3">
                  <td className="py-1">{t.trade_date}</td>
                  <td>{t.underlying}</td>
                  <td className="text-right font-mono">
                    {t.strike_price.toLocaleString()} {t.option_type}
                  </td>
                  <td className="text-right font-mono">{t.entry_premium?.toFixed(1)}</td>
                  <td className="text-right font-mono">{t.exit_premium?.toFixed(1) ?? '—'}</td>
                  <td className={clsx('text-right font-mono',
                    pnl == null ? 'text-muted' : pnl >= 0 ? 'val-bull' : 'val-bear'
                  )}>
                    {pnl != null ? `${pnl >= 0 ? '+' : ''}${pnl.toFixed(1)}%` : '—'}
                  </td>
                  <td className="text-right">{t.confidence}%</td>
                  <td className="text-right">{t.quality_grade ?? '—'}</td>
                  <td className={clsx('text-right', STATUS_COLORS[t.status] ?? 'text-muted')}>
                    {t.status}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {data && (
        <div className="flex items-center justify-between text-xs text-muted">
          <span>{data.total} trades total</span>
          <div className="flex gap-2">
            <button
              className="px-2 py-1 rounded bg-bg-surface hover:bg-white/5 disabled:opacity-40"
              disabled={page <= 1}
              onClick={() => setPage(p => p - 1)}
            >
              ← Prev
            </button>
            <span className="px-2 py-1">Page {page} / {data.pages}</span>
            <button
              className="px-2 py-1 rounded bg-bg-surface hover:bg-white/5 disabled:opacity-40"
              disabled={page >= data.pages}
              onClick={() => setPage(p => p + 1)}
            >
              Next →
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
