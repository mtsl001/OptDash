import { useState } from 'react'
import { useRecommendation, useAcceptTrade, useRejectTrade } from '../../hooks/useAI'
import { useDashboardStore } from '../../store/dashboardStore'
import type { TradeCard } from '../../types'
import clsx from 'clsx'

const REJECT_REASONS = [
  'MANUAL_OVERRIDE', 'LOW_CONFIDENCE', 'BAD_TIMING',
  'POSITION_LIMIT', 'HIGH_RISK', 'OTHER'
]

export default function AITradeCard() {
  const { data, isLoading }  = useRecommendation()
  const accept = useAcceptTrade()
  const reject = useRejectTrade()
  const { selectedSnapTime } = useDashboardStore()
  const [rejectReason, setRejectReason] = useState('MANUAL_OVERRIDE')
  const [showReject, setShowReject]     = useState(false)

  if (isLoading) {
    return (
      <div className="panel h-full">
        <div className="panel-title">AI Recommendation</div>
        <div className="text-muted text-xs">… loading</div>
      </div>
    )
  }

  if (!data || (data as any).status === 'NO_RECOMMENDATION') {
    return (
      <div className="panel h-full">
        <div className="panel-title">AI Recommendation</div>
        <div className="text-muted text-xs mt-4 text-center">No active recommendation</div>
      </div>
    )
  }

  const card = data as TradeCard
  const isCall = card.option_type === 'CE'
  const typeClass = isCall ? 'text-bull-light' : 'text-bear-light'

  return (
    <div className="panel h-full">
      <div className="panel-title flex justify-between">
        <span>AI Recommendation</span>
        <span className={clsx('font-mono text-xs', {
          'badge-go':   card.gate_verdict === 'GO',
          'badge-wait': card.gate_verdict === 'WAIT',
          'badge-nogo': card.gate_verdict === 'NO_GO',
        })}>
          {card.gate_verdict}
        </span>
      </div>

      {/* Core trade info */}
      <div className="flex gap-4 mb-3">
        <div>
          <div className="text-muted text-xs">Strike</div>
          <div className={clsx('text-lg font-mono font-semibold', typeClass)}>
            {card.strike_price.toLocaleString()} {card.option_type}
          </div>
          <div className="text-muted text-xs">{card.expiry_date} • {card.dte}DTE</div>
        </div>
        <div className="ml-auto text-right">
          <div className="text-muted text-xs">Entry</div>
          <div className="font-mono text-sm">{card.entry_premium.toFixed(1)}</div>
          <div className="text-xs">
            <span className="text-bear-light">SL {card.sl_price.toFixed(1)}</span>
            {' › '}
            <span className="text-bull-light">T {card.target_price.toFixed(1)}</span>
          </div>
        </div>
      </div>

      {/* Scores */}
      <div className="grid grid-cols-3 gap-1 mb-3 text-xs">
        <div className="bg-bg-panel rounded p-1.5 text-center">
          <div className="text-muted">Conf</div>
          <div className="font-mono">{card.confidence}%</div>
        </div>
        <div className="bg-bg-panel rounded p-1.5 text-center">
          <div className="text-muted">Gate</div>
          <div className="font-mono">{card.gate_score}</div>
        </div>
        <div className="bg-bg-panel rounded p-1.5 text-center">
          <div className="text-muted">Grade</div>
          <div className={clsx('font-mono', {
            'text-bull-light':  card.quality_grade === 'A',
            'text-brand':       card.quality_grade === 'B',
            'text-accent':      card.quality_grade === 'C',
            'text-bear-light':  card.quality_grade === 'D',
          })}>{card.quality_grade}</div>
        </div>
      </div>

      {/* Narrative */}
      {card.narrative && (
        <div className="text-xs text-muted bg-bg-panel rounded p-2 mb-3 leading-relaxed">
          {card.narrative}
        </div>
      )}

      {/* Actions */}
      {!showReject ? (
        <div className="flex gap-2">
          <button
            className="btn-primary flex-1"
            disabled={accept.isPending}
            onClick={() => accept.mutate({ tradeId: card.id, snapTime: selectedSnapTime })}
          >
            {accept.isPending ? 'Accepting…' : '✓ Accept'}
          </button>
          <button
            className="btn-danger flex-1"
            onClick={() => setShowReject(true)}
          >
            ✗ Reject
          </button>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <select
            value={rejectReason}
            onChange={e => setRejectReason(e.target.value)}
            className="bg-bg-panel border border-border-dim rounded px-2 py-1 text-xs text-gray-300 w-full"
          >
            {REJECT_REASONS.map(r => <option key={r} value={r}>{r.replace(/_/g, ' ')}</option>)}
          </select>
          <div className="flex gap-2">
            <button
              className="btn-danger flex-1"
              disabled={reject.isPending}
              onClick={() => {
                reject.mutate({ tradeId: card.id, reason: rejectReason })
                setShowReject(false)
              }}
            >
              {reject.isPending ? 'Rejecting…' : 'Confirm Reject'}
            </button>
            <button
              className="text-muted text-xs px-2 hover:text-gray-300"
              onClick={() => setShowReject(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
