import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useDashboardStore, POLL } from '../store/dashboardStore'
import {
  fetchRecommendation, fetchLivePosition,
  acceptTrade, rejectTrade, closeTrade,
  fetchTradeHistory, fetchLearningReport
} from '../api/ai'

export function useRecommendation() {
  const { underlying, snapMode } = useDashboardStore()
  return useQuery({
    queryKey:        ['recommendation', underlying],
    queryFn:         () => fetchRecommendation(underlying),
    refetchInterval: snapMode === 'LIVE' ? POLL.LIVE : false,
    staleTime:       0,
  })
}

export function useLivePosition() {
  const { underlying, snapMode } = useDashboardStore()
  return useQuery({
    queryKey:        ['position', underlying],
    queryFn:         () => fetchLivePosition(underlying),
    refetchInterval: snapMode === 'LIVE' ? POLL.LIVE : false,
    staleTime:       0,
  })
}

export function useAcceptTrade() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ tradeId, snapTime, actualEntry }: {
      tradeId: number; snapTime: string; actualEntry?: number
    }) => acceptTrade(tradeId, snapTime, actualEntry),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['recommendation'] })
      qc.invalidateQueries({ queryKey: ['position'] })
    },
  })
}

export function useRejectTrade() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ tradeId, reason, note }: {
      tradeId: number; reason: string; note?: string
    }) => rejectTrade(tradeId, reason, note),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['recommendation'] }),
  })
}

export function useCloseTrade() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ tradeId, exitPrice, snapTime }: {
      tradeId: number; exitPrice: number; snapTime: string
    }) => closeTrade(tradeId, exitPrice, snapTime),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['position'] })
      qc.invalidateQueries({ queryKey: ['recommendation'] })
    },
  })
}

export function useTradeHistory(page = 1, perPage = 20, underlying?: string, status?: string) {
  return useQuery({
    queryKey: ['history', page, perPage, underlying, status],
    queryFn:  () => fetchTradeHistory(page, perPage, underlying, status),
    staleTime: 30_000,
  })
}

export function useLearningReport(days = 30) {
  return useQuery({
    queryKey: ['learning', days],
    queryFn:  () => fetchLearningReport(days),
    staleTime: 60_000,
  })
}
