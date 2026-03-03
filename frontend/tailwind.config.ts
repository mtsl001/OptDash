import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        'bg-panel':   '#0F1923',
        'bg-surface': '#162030',
        'border-dim': '#243040',
        'brand':      '#2E75B6',
        'accent':     '#E8A020',
        'bull':       '#1E7C44',
        'bear':       '#C0392B',
        'muted':      '#6B7280',
        'bull-light': '#27AE60',
        'bear-light': '#E74C3C',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
    },
  },
  plugins: [],
} satisfies Config
