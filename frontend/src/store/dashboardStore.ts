import { create } from 'zustand'

export const POLL = {
  LIVE: 5_000,
  SLOW: 30_000,
} as const

type SnapMode = 'LIVE' | 'REPLAY'

interface DashboardStore {
  underlying:       string
  setUnderlying:    (u: string) => void
  tradeDate:        string
  setTradeDate:     (d: string) => void
  snapMode:         SnapMode
  selectedSnapTime: string
  setSnapMode:      (m: SnapMode) => void
  setSnapTime:      (t: string) => void
  soundEnabled:     boolean
  toggleSound:      () => void
  direction:        'CE' | 'PE' | null
  setDirection:     (d: 'CE' | 'PE' | null) => void
}

const today = new Date().toISOString().slice(0, 10)

export const useDashboardStore = create<DashboardStore>((set) => ({
  underlying:       'NIFTY',
  setUnderlying:    (u) => set({ underlying: u }),

  tradeDate:        today,
  setTradeDate:     (d) => set({ tradeDate: d }),

  snapMode:         'LIVE',
  selectedSnapTime: '15:25',
  setSnapMode:      (m) => set({ snapMode: m }),
  setSnapTime:      (t) => set({ selectedSnapTime: t, snapMode: 'REPLAY' }),

  soundEnabled: false,
  toggleSound:  () => set((s) => ({ soundEnabled: !s.soundEnabled })),

  direction:    null,
  setDirection: (d) => set({ direction: d }),
}))
