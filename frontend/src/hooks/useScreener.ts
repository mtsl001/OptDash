import { useQuery } from '@tanstack/react-query'
import { useDashboardStore, POLL } from '../store/dashboardStore'
import { fetchStrikes, fetchTermStructure } from '../api/screener'

export function useStrikes(topN = 20) {
  const { underlying, tradeDate, selectedSnapTime } = useDashboardStore()
  return useQuery({
    queryKey:        ['strikes', tradeDate, selectedSnapTime, underlying, topN],
    queryFn:         () => fetchStrikes(tradeDate, selectedSnapTime, underlying, topN),
    refetchInterval: POLL.SLOW,
    staleTime:       0,
  })
}

export function useTermStructure() {
  const { underlying, tradeDate, selectedSnapTime } = useDashboardStore()
  return useQuery({
    queryKey:        ['termstructure', tradeDate, selectedSnapTime, underlying],
    queryFn:         () => fetchTermStructure(tradeDate, selectedSnapTime, underlying),
    refetchInterval: POLL.SLOW,
    staleTime:       0,
  })
}
