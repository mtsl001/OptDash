import EnvironmentPanel  from '../components/panels/EnvironmentPanel'
import GEXPanel          from '../components/panels/GEXPanel'
import CoCPanel          from '../components/panels/CoCPanel'
import PCRPanel          from '../components/panels/PCRPanel'
import VexCexPanel       from '../components/panels/VexCexPanel'
import AlertsPanel       from '../components/panels/AlertsPanel'
import StrikeScreener    from '../components/panels/StrikeScreener'
import AITradeCard       from '../components/panels/AITradeCard'
import LivePositionPanel from '../components/panels/LivePositionPanel'

export default function DashboardPage() {
  return (
    <div className="grid gap-2" style={{ gridTemplateColumns: '1fr 1fr 1fr', gridTemplateRows: 'auto' }}>

      {/* Row 1: Environment (full width) */}
      <div className="col-span-3">
        <EnvironmentPanel />
      </div>

      {/* Row 2: GEX | CoC | PCR */}
      <GEXPanel />
      <CoCPanel />
      <PCRPanel />

      {/* Row 3: VEX/CEX | Alerts | AI Card */}
      <VexCexPanel />
      <AlertsPanel />
      <AITradeCard />

      {/* Row 4: Strike Screener (2 col) | Live Position */}
      <div className="col-span-2">
        <StrikeScreener />
      </div>
      <LivePositionPanel />

    </div>
  )
}
