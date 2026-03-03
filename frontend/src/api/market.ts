import { api } from './client'
import type { SpotData, GEXRow, CoCRow, EnvironmentScore } from '../types'

export const fetchSpot = (tradeDate: string, underlying: string) =>
  api.get<SpotData>('/market/spot', { params: { trade_date: tradeDate, underlying } }).then(r => r.data)

export const fetchGEX = (tradeDate: string, underlying: string) =>
  api.get<GEXRow[]>('/market/gex', { params: { trade_date: tradeDate, underlying } }).then(r => r.data)

export const fetchCoC = (tradeDate: string, underlying: string) =>
  api.get<CoCRow[]>('/market/coc', { params: { trade_date: tradeDate, underlying } }).then(r => r.data)

export const fetchEnvironment = (
  tradeDate: string, snapTime: string, underlying: string, direction?: string | null
) =>
  api.get<EnvironmentScore>('/market/environment', {
    params: { trade_date: tradeDate, snap_time: snapTime, underlying, direction: direction ?? undefined }
  }).then(r => r.data)
