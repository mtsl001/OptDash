import { create } from 'zustand'
import type { PositionLive, TradeCard } from '../types'

interface PositionStore {
  activePosition: PositionLive | null
  pendingCard:    TradeCard | null
  setPosition:    (p: PositionLive | null) => void
  setPending:     (c: TradeCard | null) => void
}

export const usePositionStore = create<PositionStore>((set) => ({
  activePosition: null,
  pendingCard:    null,
  setPosition:    (p) => set({ activePosition: p }),
  setPending:     (c) => set({ pendingCard: c }),
}))
