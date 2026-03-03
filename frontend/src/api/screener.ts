import { api } from './client'
import type { StrikeRow, TermStructureResponse } from '../types'

export const fetchStrikes = (tradeDate: string, snapTime: string, underlying: string, topN = 20) =>
  api.get<StrikeRow[]>('/screener/strikes', {
    params: { trade_date: tradeDate, snap_time: snapTime, underlying, top_n: topN }
  }).then(r => r.data)

export const fetchTermStructure = (tradeDate: string, snapTime: string, underlying: string) =>
  api.get<TermStructureResponse>('/screener/term-structure', {
    params: { trade_date: tradeDate, snap_time: snapTime, underlying }
  }).then(r => r.data)
