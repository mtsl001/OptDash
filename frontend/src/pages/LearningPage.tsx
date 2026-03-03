import { useState } from 'react'
import { useLearningReport } from '../hooks/useAI'
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Cell
} from 'recharts'
import clsx from 'clsx'

export default function LearningPage() {
  const [days, setDays] = useState(30)
  const { data, isLoading } = useLearningReport(days)

  const daysOptions = [7, 14, 30, 60, 90]

  if (isLoading || !data) {
    return (
      <div className="panel">
        <div className="panel-title">Learning Report</div>
        <div className="text-muted text-xs">… loading</div>
      </div>
    )
  }

  const { overall, by_underlying, by_direction, confidence_buckets, gate_buckets, shadow_summary } = data

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <h1 className="text-sm font-semibold text-gray-200">Learning Report</h1>
        <div className="flex gap-1">
          {daysOptions.map(d => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={clsx(
                'text-xs px-2 py-1 rounded',
                days === d ? 'bg-brand text-white' : 'text-muted hover:text-gray-300'
              )}
            >
              {d}D
            </button>
          ))}
        </div>
      </div>

      {/* Overall KPIs */}
      <div className="grid grid-cols-5 gap-2">
        {[
          { label: 'Win Rate',    value: `${overall.win_rate}%`,    cls: overall.win_rate >= 50 ? 'val-bull' : 'val-bear' },
          { label: 'Avg P&L',    value: `${overall.avg_pnl > 0 ? '+' : ''}${overall.avg_pnl}%`, cls: overall.avg_pnl >= 0 ? 'val-bull' : 'val-bear' },
          { label: 'Trades',     value: overall.total_trades,    cls: 'val-neutral' },
          { label: 'Avg Conf',   value: `${overall.avg_confidence}%`, cls: 'val-neutral' },
          { label: 'Avg Gate',   value: overall.avg_gate,         cls: 'val-neutral' },
        ].map(({ label, value, cls }) => (
          <div key={label} className="panel text-center">
            <div className="text-muted text-xs mb-1">{label}</div>
            <div className={clsx('font-mono text-base font-semibold', cls)}>{value}</div>
          </div>
        ))}
      </div>

      {/* Confidence buckets */}
      <div className="panel">
        <div className="panel-title">Win Rate by Confidence Bucket</div>
        <ResponsiveContainer width="100%" height={120}>
          <BarChart data={confidence_buckets} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
            <XAxis dataKey="bucket" tick={{ fill: '#6B7280', fontSize: 9 }} />
            <YAxis tick={{ fill: '#6B7280', fontSize: 9 }} tickLine={false} axisLine={false} domain={[0, 100]} />
            <Tooltip contentStyle={{ background: '#162030', border: '1px solid #243040', fontSize: 11 }}
              formatter={(v: number) => [`${v?.toFixed(1)}%`, 'Win Rate']} />
            <Bar dataKey="win_rate" radius={[2, 2, 0, 0]}>
              {confidence_buckets.map((e: any, i: number) => (
                <Cell key={i} fill={(e.win_rate ?? 0) >= 55 ? '#1E7C44' : (e.win_rate ?? 0) >= 45 ? '#E8A020' : '#C0392B'} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Gate score buckets */}
      <div className="panel">
        <div className="panel-title">Win Rate by Gate Score</div>
        <ResponsiveContainer width="100%" height={120}>
          <BarChart data={gate_buckets} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
            <XAxis dataKey="bucket" tick={{ fill: '#6B7280', fontSize: 9 }} />
            <YAxis tick={{ fill: '#6B7280', fontSize: 9 }} tickLine={false} axisLine={false} domain={[0, 100]} />
            <Tooltip contentStyle={{ background: '#162030', border: '1px solid #243040', fontSize: 11 }} />
            <Bar dataKey="win_rate" fill="#2E75B6" radius={[2, 2, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* By underlying + direction side by side */}
      <div className="grid grid-cols-2 gap-2">
        <div className="panel">
          <div className="panel-title">By Underlying</div>
          <table className="w-full text-xs">
            <thead><tr className="text-muted">
              <th className="text-left">Underlying</th>
              <th className="text-right">Trades</th>
              <th className="text-right">Win%</th>
              <th className="text-right">Avg PnL</th>
            </tr></thead>
            <tbody>
              {by_underlying.map((r: any) => (
                <tr key={r.underlying} className="border-b border-border-dim/40">
                  <td className="py-0.5">{r.underlying}</td>
                  <td className="text-right">{r.total}</td>
                  <td className={clsx('text-right', (r.win_rate ?? 0) >= 50 ? 'val-bull' : 'val-bear')}>
                    {r.win_rate?.toFixed(1)}%
                  </td>
                  <td className={clsx('text-right', (r.avg_pnl ?? 0) >= 0 ? 'val-bull' : 'val-bear')}>
                    {r.avg_pnl > 0 ? '+' : ''}{r.avg_pnl?.toFixed(1)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="panel">
          <div className="panel-title">Shadow Trade Comparison</div>
          {shadow_summary ? (
            <div className="space-y-2 text-xs">
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <div className="text-muted">Total Shadows</div>
                  <div className="val-neutral text-base font-mono">{shadow_summary.total}</div>
                </div>
                <div>
                  <div className="text-muted">Shadow Win%</div>
                  <div className={clsx('text-base font-mono', (shadow_summary.win_rate ?? 0) >= 50 ? 'val-bull' : 'val-bear')}>
                    {shadow_summary.win_rate?.toFixed(1) ?? '—'}%
                  </div>
                </div>
                <div>
                  <div className="text-muted">Avg P&L</div>
                  <div className={clsx('font-mono', (shadow_summary.avg_pnl ?? 0) >= 0 ? 'val-bull' : 'val-bear')}>
                    {shadow_summary.avg_pnl > 0 ? '+' : ''}{shadow_summary.avg_pnl?.toFixed(1)}%
                  </div>
                </div>
                <div>
                  <div className="text-muted">Outcomes</div>
                  <div className="text-gray-300">
                    {Object.entries(shadow_summary.outcomes ?? {}).map(([k, v]: any) => `${k}:${v}`).join(', ')}
                  </div>
                </div>
              </div>
            </div>
          ) : <div className="text-muted text-xs">No shadow data</div>}
        </div>
      </div>
    </div>
  )
}
