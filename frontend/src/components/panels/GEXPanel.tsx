import { useGEX } from '../../hooks/useMarket'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, ReferenceLine
} from 'recharts'

export default function GEXPanel() {
  const { data: series, isLoading } = useGEX()

  if (isLoading || !series?.length) {
    return (
      <div className="panel h-full">
        <div className="panel-title">GEX</div>
        <div className="text-muted text-xs">… loading</div>
      </div>
    )
  }

  const latest = series[series.length - 1]
  const positive = (latest?.gex_all_B ?? 0) >= 0

  return (
    <div className="panel h-full flex flex-col">
      <div className="panel-title flex justify-between">
        <span>GEX</span>
        <span className={positive ? 'val-bull' : 'val-bear'}>
          {positive ? 'POSITIVE' : 'NEGATIVE'} {latest?.gex_all_B?.toFixed(1)}B
        </span>
      </div>

      <ResponsiveContainer width="100%" height={110}>
        <AreaChart data={series} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
          <defs>
            <linearGradient id="gexGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor="#2E75B6" stopOpacity={0.4} />
              <stop offset="95%" stopColor="#2E75B6" stopOpacity={0}   />
            </linearGradient>
          </defs>
          <XAxis dataKey="snap_time" tick={{ fill: '#6B7280', fontSize: 9 }} tickLine={false} interval="preserveStartEnd" />
          <YAxis tick={{ fill: '#6B7280', fontSize: 9 }} tickLine={false} axisLine={false} />
          <ReferenceLine y={0} stroke="#243040" />
          <Tooltip
            contentStyle={{ background: '#162030', border: '1px solid #243040', fontSize: 11 }}
            formatter={(v: number) => [`${v.toFixed(2)}B`, 'GEX']}
          />
          <Area type="monotone" dataKey="gex_all_B" stroke="#2E75B6" fill="url(#gexGrad)" strokeWidth={1.5} dot={false} />
        </AreaChart>
      </ResponsiveContainer>

      <div className="grid grid-cols-3 gap-1 mt-1 text-xs">
        <div>
          <div className="text-muted">Near</div>
          <div className="val-neutral">{latest?.gex_near_B?.toFixed(1)}B</div>
        </div>
        <div>
          <div className="text-muted">Far</div>
          <div className="val-neutral">{latest?.gex_far_B?.toFixed(1)}B</div>
        </div>
        <div>
          <div className="text-muted">% Peak</div>
          <div className="val-neutral">{latest?.pct_of_peak?.toFixed(0)}%</div>
        </div>
      </div>
    </div>
  )
}
