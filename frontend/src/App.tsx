import { Routes, Route, NavLink } from 'react-router-dom'
import { LayoutDashboard, BookOpen, Brain, Settings } from 'lucide-react'
import DashboardPage from './pages/DashboardPage'
import JournalPage   from './pages/JournalPage'
import LearningPage  from './pages/LearningPage'
import GlobalHeader  from './components/GlobalHeader'
import clsx from 'clsx'

const navItems = [
  { to: '/',         icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/journal',  icon: BookOpen,        label: 'Journal'   },
  { to: '/learning', icon: Brain,           label: 'Learning'  },
]

export default function App() {
  return (
    <div className="min-h-screen bg-bg-panel flex flex-col">
      <GlobalHeader />

      {/* Side nav */}
      <div className="flex flex-1 overflow-hidden">
        <nav className="w-14 bg-bg-surface border-r border-border-dim flex flex-col items-center py-4 gap-6 shrink-0">
          {navItems.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              title={label}
              className={({ isActive }) =>
                clsx(
                  'p-2 rounded-lg transition-colors',
                  isActive
                    ? 'bg-brand/20 text-brand'
                    : 'text-muted hover:text-gray-200 hover:bg-white/5'
                )
              }
            >
              <Icon size={18} />
            </NavLink>
          ))}
        </nav>

        <main className="flex-1 overflow-auto p-3">
          <Routes>
            <Route path="/"         element={<DashboardPage />} />
            <Route path="/journal"  element={<JournalPage />}   />
            <Route path="/learning" element={<LearningPage />}  />
          </Routes>
        </main>
      </div>
    </div>
  )
}
