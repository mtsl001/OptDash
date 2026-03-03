import { api } from './client'
import type { PCRRow, Alert, VolumeVelocityRow, VexCexResponse } from '../types'

export const fetchPCR = (tradeDate: string, underlying: string) =>
  api.get<PCRRow[]>('/micro/pcr', { params: { trade_date: tradeDate, underlying } }).then(r => r.data)

export const fetchAlerts = (tradeDate: string, snapTime: string, underlying: string) =>
  api.get<Alert[]>('/micro/alerts', { params: { trade_date: tradeDate, snap_time: snapTime, underlying } }).then(r => r.data)

export const fetchVolumeVelocity = (tradeDate: string, underlying: string) =>
  api.get<VolumeVelocityRow[]>('/micro/volume-velocity', { params: { trade_date: tradeDate, underlying } }).then(r => r.data)

export const fetchVexCex = (tradeDate: string, snapTime: string, underlying: string) =>
  api.get<VexCexResponse>('/micro/vex-cex', {
    params: { trade_date: tradeDate, snap_time: snapTime, underlying }
  }).then(r => r.data)
