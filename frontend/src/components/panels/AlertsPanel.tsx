import { useAlerts } from '../../hooks/useMicro'
import clsx from 'clsx'
import type { AlertSeverity } from '../../types'

const SEV_CLASS: Record<AlertSeverity, string> = {
  HIGH:   'text-bear-light',
  MEDIUM: 'text-accent',
  LOW:    'text-muted',
}

export default function AlertsPanel() {
  const { data: alerts, isLoading } = useAlerts()

  return (
    <div className="panel h-full">
      <div className="panel-title">Alerts</div>
      {isLoading && <div className="text-muted text-xs">… loading</div>}
      {!isLoading && (!alerts?.length) && (
        <div className="text-muted text-xs">No alerts</div>
      )}
      <div className="flex flex-col gap-1 overflow-y-auto max-h-52">
        {alerts?.slice(0, 8).map((a, i) => (
          <div key={i} className="flex gap-2 text-xs border-b border-border-dim pb-1">
            <span className="text-muted w-10 shrink-0">{a.time.slice(0, 5)}</span>
            <span className={clsx('shrink-0', SEV_CLASS[a.severity])}>
              {a.severity === 'HIGH' ? '●' : a.severity === 'MEDIUM' ? '◑' : '○'}
            </span>
            <div>
              <div className="text-gray-200">{a.headline}</div>
              <div className="text-muted">{a.message}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
