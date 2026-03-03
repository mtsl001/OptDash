import { useStrikes } from '../../hooks/useScreener'
import clsx from 'clsx'

const STAR_COLORS = ['', 'text-muted', 'text-gray-300', 'text-accent', 'text-bull-light']

export default function StrikeScreener() {
  const { data: rows, isLoading } = useStrikes()

  return (
    <div className="panel h-full">
      <div className="panel-title">Strike Screener — Top S_Score</div>
      {isLoading && <div className="text-muted text-xs">… loading</div>}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted border-b border-border-dim">
              <th className="text-left pb-1">Type</th>
              <th className="text-right pb-1">Strike</th>
              <th className="text-right pb-1">LTP</th>
              <th className="text-right pb-1">IV%</th>
              <th className="text-right pb-1">δ</th>
              <th className="text-right pb-1">θ</th>
              <th className="text-right pb-1">DTE</th>
              <th className="text-right pb-1">S★</th>
              <th className="text-right pb-1">★</th>
            </tr>
          </thead>
          <tbody>
            {rows?.map((r, i) => (
              <tr
                key={i}
                className={clsx(
                  'border-b border-border-dim/40 hover:bg-white/3',
                  r.option_type === 'CE' ? 'text-bull-light/90' : 'text-bear-light/90'
                )}
              >
                <td className="py-0.5">{r.option_type}</td>
                <td className="text-right font-mono">{r.strike_price.toLocaleString()}</td>
                <td className="text-right font-mono">{r.ltp.toFixed(1)}</td>
                <td className="text-right">{r.iv.toFixed(1)}</td>
                <td className="text-right">{r.delta.toFixed(2)}</td>
                <td className="text-right text-bear-light">{r.theta.toFixed(1)}</td>
                <td className="text-right">{r.dte}</td>
                <td className="text-right font-semibold">{r.s_score.toFixed(1)}</td>
                <td className={clsx('text-right', STAR_COLORS[r.stars])}>{'★'.repeat(r.stars)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
