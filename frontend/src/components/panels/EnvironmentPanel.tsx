import { useEnvironment } from '../../hooks/useMarket'
import clsx from 'clsx'

const CONDITION_LABELS: Record<string, string> = {
  trend_bullish:   'Trend Bullish',  trend_bearish:  'Trend Bearish',
  gex_positive:    'GEX Regime',     coc_bullish:    'CoC Bullish',
  coc_bearish:     'CoC Bearish',    pcr_favorable:  'PCR Favorable',
  iv_normal:       'IV Normal',      volume_ok:      'Volume OK',
  no_spike:        'No Spike',       direction_conf: 'Dir Confidence',
  theta_burn:      'Theta Burn OK',
}

export default function EnvironmentPanel() {
  const { data: env, isLoading } = useEnvironment()

  if (isLoading || !env) {
    return (
      <div className="panel h-full">
        <div className="panel-title">Environment Gate</div>
        <div className="text-muted text-xs">… loading</div>
      </div>
    )
  }

  const verdictClass =
    env.verdict === 'GO'    ? 'badge-go'   :
    env.verdict === 'WAIT'  ? 'badge-wait' : 'badge-nogo'

  return (
    <div className="panel h-full">
      <div className="panel-title flex justify-between">
        <span>Environment Gate</span>
        <span className={verdictClass}>{env.verdict}</span>
      </div>

      {/* Score bar */}
      <div className="flex items-center gap-2 mb-3">
        <div className="flex-1 bg-bg-panel rounded-full h-2">
          <div
            className={clsx('h-2 rounded-full transition-all', {
              'bg-bull-light': env.verdict === 'GO',
              'bg-accent':     env.verdict === 'WAIT',
              'bg-bear-light': env.verdict === 'NO_GO',
            })}
            style={{ width: `${(env.score / env.max_score) * 100}%` }}
          />
        </div>
        <span className="font-mono text-xs text-gray-300">{env.score}/{env.max_score}</span>
      </div>

      {/* Conditions grid */}
      <div className="grid grid-cols-2 gap-x-3 gap-y-1">
        {Object.entries(env.conditions).map(([key, cond]) => (
          <div key={key} className="flex items-center gap-1.5 text-xs">
            <span className={cond.met ? 'text-bull-light' : 'text-bear-light'}>
              {cond.met ? '✓' : '✗'}
            </span>
            <span className="text-muted">{CONDITION_LABELS[key] ?? key}</span>
            <span className="ml-auto text-gray-400 font-mono">
              {typeof cond.value === 'number' ? cond.value.toFixed(1) : cond.value}
            </span>
          </div>
        ))}
      </div>

      <div className="mt-2 text-xs text-muted">
        Session: <span className="text-gray-300">{env.session}</span>
      </div>
    </div>
  )
}
