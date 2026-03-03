import { useQuery } from '@tanstack/react-query'
import { useDashboardStore, POLL } from '../store/dashboardStore'
import { fetchPCR, fetchAlerts, fetchVolumeVelocity, fetchVexCex } from '../api/micro'

export function usePCR() {
  const { underlying, tradeDate, snapMode } = useDashboardStore()
  return useQuery({
    queryKey:        ['pcr', tradeDate, underlying],
    queryFn:         () => fetchPCR(tradeDate, underlying),
    refetchInterval: snapMode === 'LIVE' ? POLL.LIVE : false,
    staleTime:       0,
  })
}

export function useAlerts() {
  const { underlying, tradeDate, selectedSnapTime, snapMode } = useDashboardStore()
  return useQuery({
    queryKey:        ['alerts', tradeDate, selectedSnapTime, underlying],
    queryFn:         () => fetchAlerts(tradeDate, selectedSnapTime, underlying),
    refetchInterval: snapMode === 'LIVE' ? POLL.LIVE : false,
    staleTime:       0,
  })
}

export function useVolumeVelocity() {
  const { underlying, tradeDate, snapMode } = useDashboardStore()
  return useQuery({
    queryKey:        ['volume', tradeDate, underlying],
    queryFn:         () => fetchVolumeVelocity(tradeDate, underlying),
    refetchInterval: snapMode === 'LIVE' ? POLL.LIVE : false,
    staleTime:       0,
  })
}

export function useVexCex() {
  const { underlying, tradeDate, selectedSnapTime, snapMode } = useDashboardStore()
  return useQuery({
    queryKey:        ['vexcex', tradeDate, selectedSnapTime, underlying],
    queryFn:         () => fetchVexCex(tradeDate, selectedSnapTime, underlying),
    refetchInterval: snapMode === 'LIVE' ? POLL.LIVE : false,
    staleTime:       0,
  })
}
