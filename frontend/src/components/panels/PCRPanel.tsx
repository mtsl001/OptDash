import { usePCR } from '../../hooks/useMicro'
import {
  ResponsiveContainer, ComposedChart, Bar, Line, XAxis, YAxis, Tooltip
} from 'recharts'

export default function PCRPanel() {
  const { data: series, isLoading } = usePCR()

  if (isLoading || !series?.length) {
    return (
      <div className="panel h-full">
        <div className="panel-title">PCR</div>
        <div className="text-muted text-xs">… loading</div>
      </div>
    )
  }

  const latest = series[series.length - 1]

  return (
    <div className="panel h-full flex flex-col">
      <div className="panel-title flex justify-between">
        <span>PCR</span>
        <span className="text-xs text-gray-300">{latest?.signal?.replace(/_/g, ' ')}</span>
      </div>

      <ResponsiveContainer width="100%" height={110}>
        <ComposedChart data={series} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
          <XAxis dataKey="snap_time" tick={{ fill: '#6B7280', fontSize: 9 }} tickLine={false} interval="preserveStartEnd" />
          <YAxis tick={{ fill: '#6B7280', fontSize: 9 }} tickLine={false} axisLine={false} />
          <Tooltip contentStyle={{ background: '#162030', border: '1px solid #243040', fontSize: 11 }} />
          <Bar dataKey="pcr_oi"  fill="#2E75B6" opacity={0.5} />
          <Line type="monotone" dataKey="smoothed_obi" stroke="#E8A020" strokeWidth={1.5} dot={false} />
        </ComposedChart>
      </ResponsiveContainer>

      <div className="grid grid-cols-3 gap-1 mt-1 text-xs">
        <div>
          <div className="text-muted">PCR OI</div>
          <div className="val-neutral">{latest?.pcr_oi?.toFixed(2)}</div>
        </div>
        <div>
          <div className="text-muted">PCR Vol</div>
          <div className="val-neutral">{latest?.pcr_vol?.toFixed(2)}</div>
        </div>
        <div>
          <div className="text-muted">Divergence</div>
          <div className="val-neutral">{latest?.pcr_divergence?.toFixed(2)}</div>
        </div>
      </div>
    </div>
  )
}
