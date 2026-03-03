import { useQuery } from '@tanstack/react-query'
import { useDashboardStore, POLL } from '../store/dashboardStore'
import { fetchSpot, fetchGEX, fetchCoC, fetchEnvironment } from '../api/market'

export function useSpot() {
  const { underlying, tradeDate, snapMode } = useDashboardStore()
  return useQuery({
    queryKey:        ['spot', tradeDate, underlying],
    queryFn:         () => fetchSpot(tradeDate, underlying),
    refetchInterval: snapMode === 'LIVE' ? POLL.LIVE : false,
    staleTime:       0,
  })
}

export function useGEX() {
  const { underlying, tradeDate, snapMode } = useDashboardStore()
  return useQuery({
    queryKey:        ['gex', tradeDate, underlying],
    queryFn:         () => fetchGEX(tradeDate, underlying),
    refetchInterval: snapMode === 'LIVE' ? POLL.LIVE : false,
    staleTime:       0,
  })
}

export function useCoC() {
  const { underlying, tradeDate, snapMode } = useDashboardStore()
  return useQuery({
    queryKey:        ['coc', tradeDate, underlying],
    queryFn:         () => fetchCoC(tradeDate, underlying),
    refetchInterval: snapMode === 'LIVE' ? POLL.LIVE : false,
    staleTime:       0,
  })
}

export function useEnvironment() {
  const { underlying, tradeDate, selectedSnapTime, snapMode, direction } = useDashboardStore()
  return useQuery({
    queryKey:        ['environment', tradeDate, selectedSnapTime, underlying, direction],
    queryFn:         () => fetchEnvironment(tradeDate, selectedSnapTime, underlying, direction),
    refetchInterval: snapMode === 'LIVE' ? POLL.LIVE : false,
    staleTime:       0,
  })
}
