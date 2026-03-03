import { useVexCex } from '../../hooks/useMicro'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip
} from 'recharts'

export default function VexCexPanel() {
  const { data: res, isLoading } = useVexCex()

  if (isLoading || !res?.series?.length) {
    return (
      <div className="panel h-full">
        <div className="panel-title">VEX / CEX</div>
        <div className="text-muted text-xs">… loading</div>
      </div>
    )
  }

  const cur = res.current

  return (
    <div className="panel h-full flex flex-col">
      <div className="panel-title flex justify-between">
        <span>VEX / CEX</span>
        {res.dealer_oclock && (
          <span className="badge-wait">Dealer O'Clock</span>
        )}
      </div>

      <ResponsiveContainer width="100%" height={110}>
        <AreaChart data={res.series} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
          <defs>
            <linearGradient id="vexGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor="#E8A020" stopOpacity={0.35} />
              <stop offset="95%" stopColor="#E8A020" stopOpacity={0}    />
            </linearGradient>
          </defs>
          <XAxis dataKey="snap_time" tick={{ fill: '#6B7280', fontSize: 9 }} tickLine={false} interval="preserveStartEnd" />
          <YAxis tick={{ fill: '#6B7280', fontSize: 9 }} tickLine={false} axisLine={false} />
          <Tooltip contentStyle={{ background: '#162030', border: '1px solid #243040', fontSize: 11 }} />
          <Area type="monotone" dataKey="vex_total_M" stroke="#E8A020" fill="url(#vexGrad)" strokeWidth={1.5} dot={false} />
          <Area type="monotone" dataKey="cex_total_M" stroke="#2E75B6" fill="none"           strokeWidth={1}   dot={false} />
        </AreaChart>
      </ResponsiveContainer>

      {cur && (
        <div className="text-xs text-muted mt-1 italic">{cur.interpretation}</div>
      )}
    </div>
  )
}
