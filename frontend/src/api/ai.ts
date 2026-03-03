import { api } from './client'
import type { TradeCard, PositionLive } from '../types'

export const fetchRecommendation = (underlying: string) =>
  api.get<TradeCard | { status: string }>('/ai/recommendation/latest', { params: { underlying } }).then(r => r.data)

export const fetchLivePosition = (underlying: string) =>
  api.get<PositionLive | { status: string }>('/ai/position/live', { params: { underlying } }).then(r => r.data)

export const acceptTrade = (tradeId: number, snapTime: string, actualEntry?: number) =>
  api.post('/ai/accept', { trade_id: tradeId, snap_time: snapTime, actual_entry_price: actualEntry }).then(r => r.data)

export const rejectTrade = (tradeId: number, reason: string, note?: string) =>
  api.post('/ai/reject', { trade_id: tradeId, reason, note }).then(r => r.data)

export const closeTrade = (tradeId: number, exitPrice: number, snapTime: string) =>
  api.post('/ai/close-trade', { trade_id: tradeId, exit_price: exitPrice, snap_time: snapTime }).then(r => r.data)

export const fetchTradeHistory = (page = 1, perPage = 20, underlying?: string, status?: string) =>
  api.get('/ai/journal/history', { params: { page, per_page: perPage, underlying, status } }).then(r => r.data)

export const fetchLearningReport = (days = 30) =>
  api.get('/ai/learning/report', { params: { days } }).then(r => r.data)
