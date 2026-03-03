import { useCoC } from '../../hooks/useMarket'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, ReferenceLine
} from 'recharts'

export default function CoCPanel() {
  const { data: series, isLoading } = useCoC()

  if (isLoading || !series?.length) {
    return (
      <div className="panel h-full">
        <div className="panel-title">Cost of Carry</div>
        <div className="text-muted text-xs">… loading</div>
      </div>
    )
  }

  const latest = series[series.length - 1]
  const bull    = ['VELOCITY_BULL', 'DISCOUNT'].includes(latest?.signal ?? '')
  const signalClass = bull ? 'val-bull' : (latest?.signal === 'VELOCITY_BEAR' ? 'val-bear' : 'val-neutral')

  return (
    <div className="panel h-full flex flex-col">
      <div className="panel-title flex justify-between">
        <span>Cost of Carry</span>
        <span className={signalClass}>{latest?.signal}</span>
      </div>

      <ResponsiveContainer width="100%" height={110}>
        <LineChart data={series} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
          <XAxis dataKey="snap_time" tick={{ fill: '#6B7280', fontSize: 9 }} tickLine={false} interval="preserveStartEnd" />
          <YAxis tick={{ fill: '#6B7280', fontSize: 9 }} tickLine={false} axisLine={false} />
          <ReferenceLine y={0} stroke="#243040" />
          <Tooltip contentStyle={{ background: '#162030', border: '1px solid #243040', fontSize: 11 }} />
          <Line type="monotone" dataKey="coc"       stroke="#E8A020" strokeWidth={1.5} dot={false} />
          <Line type="monotone" dataKey="v_coc_15m" stroke="#2E75B6" strokeWidth={1}   dot={false} strokeDasharray="3 2" />
        </LineChart>
      </ResponsiveContainer>

      <div className="grid grid-cols-3 gap-1 mt-1 text-xs">
        <div>
          <div className="text-muted">Fut</div>
          <div className="val-neutral">{latest?.fut_price?.toFixed(1)}</div>
        </div>
        <div>
          <div className="text-muted">CoC</div>
          <div className="val-neutral">{latest?.coc?.toFixed(2)}</div>
        </div>
        <div>
          <div className="text-muted">Velocity</div>
          <div className="val-neutral">{latest?.v_coc_15m?.toFixed(2)}</div>
        </div>
      </div>
    </div>
  )
}
