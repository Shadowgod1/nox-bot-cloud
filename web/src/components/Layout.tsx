import { NavLink, Outlet } from 'react-router'
import {
  CalendarCheck,
  FlaskConical,
  LayoutDashboard,
  LogOut,
  MessagesSquare,
  Moon,
  PlugZap,
  Settings2,
  SlidersHorizontal,
  Sun,
  type LucideIcon,
} from 'lucide-react'
import { setTheme, useBrand, useTheme } from '../brand'
import { useAuth } from '../hooks/useAuth'
import { ru } from '../i18n/ru'
import { StatusBanner } from './StatusBanner'
import { BotPausedBanner, BotPauseSwitch, useBotPause } from './BotPause'
import { cx } from './ui'
import type { ReactNode } from 'react'

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  end?: boolean
  ownerOnly?: boolean
}

const NAV: NavItem[] = [
  { to: '/', label: ru.nav.dashboard, icon: LayoutDashboard, end: true },
  { to: '/conversations', label: ru.nav.conversations, icon: MessagesSquare },
  { to: '/bookings', label: ru.nav.bookings, icon: CalendarCheck },
  { to: '/bot', label: ru.nav.bot, icon: SlidersHorizontal },
  { to: '/test-chat', label: ru.nav.testChat, icon: FlaskConical },
  { to: '/integrations', label: ru.nav.integrations, icon: PlugZap },
  { to: '/system', label: ru.nav.system, icon: Settings2 },
]

export function Wordmark({ className }: { className?: string }) {
  const brand = useBrand()
  if (brand.id === 'nox') {
    return (
      <span className={cx('inline-flex items-center gap-2 font-bold text-ink select-none', className)} aria-label={brand.name}>
        <img src={brand.favicon} alt="" className="size-[1.4em] shrink-0" aria-hidden />
        <span>{brand.name}</span>
      </span>
    )
  }
  return (
    <span className={cx('font-semibold tracking-[0.14em] text-ink select-none', className)} aria-label={brand.name}>
      {brand.name}
    </span>
  )
}

/** Light/dark switch: only brands with themes (Nox) render it. */
export function ThemeToggle({ className }: { className?: string }) {
  const brand = useBrand()
  const theme = useTheme()
  if (!brand.themes) return null
  const dark = theme === 'dark'
  const label = dark ? 'Светлая тема' : 'Тёмная тема'
  return (
    <button
      type="button"
      onClick={() => setTheme(dark ? 'light' : 'dark')}
      title={label}
      aria-label={label}
      className={cx(
        'app-nav-link flex h-9 w-full items-center gap-2.5 rounded-[7px] px-2.5 text-sm text-ink-2 hover:bg-sunken hover:text-ink max-lg:justify-center',
        className,
      )}
    >
      {dark ? <Sun className="size-[18px] shrink-0 text-ink-3" aria-hidden /> : <Moon className="size-[18px] shrink-0 text-ink-3" aria-hidden />}
      <span className="max-lg:sr-only">{label}</span>
    </button>
  )
}

export function Layout() {
  const { user, logout } = useAuth()
  const brand = useBrand()

  return (
    <div className="flex h-dvh min-h-0">
      <aside className="app-sidebar flex w-[64px] shrink-0 flex-col border-r border-line bg-canvas lg:w-[232px]" aria-label="Основная навигация">
        <div className="flex h-14 items-center px-5 max-lg:justify-center max-lg:px-0">
          <Wordmark className="text-[17px] max-lg:text-[13px] max-lg:tracking-[0.08em]" />
          <span className="ml-2 hidden text-[12px] text-ink-3 lg:inline">админка</span>
        </div>
        <nav className="flex flex-1 flex-col gap-0.5 px-2.5 py-2">
          {brand.id === 'nox' && (
            <p className="app-nav-section mb-1 hidden px-2.5 text-[11px] font-bold tracking-[0.08em] uppercase lg:block">Меню</p>
          )}
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              title={item.label}
              className={({ isActive }) =>
                cx(
                  'app-nav-link group flex h-9 items-center gap-2.5 rounded-[7px] px-2.5 text-sm transition-colors duration-100 max-lg:justify-center',
                  isActive ? 'bg-surface font-medium text-ink shadow-[0_1px_2px_oklch(25%_0.02_230/0.10)] ring-1 ring-line' : 'text-ink-2 hover:bg-sunken hover:text-ink',
                )
              }
            >
              {({ isActive }) => (
                <>
                  <item.icon className={cx('size-[18px] shrink-0', isActive ? 'text-accent' : 'text-ink-3 group-hover:text-ink-2')} aria-hidden />
                  <span className="truncate max-lg:sr-only">{item.label}</span>
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-line p-2.5">
          {user && (
            <div className="mb-1 hidden px-2.5 py-1.5 lg:block">
              <p className="truncate text-[13px] font-medium text-ink">{user.name || user.email}</p>
              <p className="truncate text-[12px] text-ink-3">{ru.roles[user.role]}</p>
            </div>
          )}
          <ThemeToggle />
          <button
            type="button"
            onClick={() => void logout()}
            title={ru.common.logout}
            className="app-nav-link flex h-9 w-full items-center gap-2.5 rounded-[7px] px-2.5 text-sm text-ink-2 hover:bg-sunken hover:text-ink max-lg:justify-center"
          >
            <LogOut className="size-[18px] shrink-0 text-ink-3" aria-hidden />
            <span className="max-lg:sr-only">{ru.common.logout}</span>
          </button>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <StatusBanner />
        <main className="min-h-0 flex-1 overflow-y-auto" id="main">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

/** Thin header over every page: the global "bot on pause" switch, plus a banner while paused. */
function TopBar() {
  const pause = useBotPause()
  return (
    <>
      <header className="flex h-12 shrink-0 items-center justify-end gap-3 border-b border-line bg-surface px-4 sm:px-6 lg:px-8">
        <BotPauseSwitch enabled={pause.enabled} busy={pause.busy} onToggle={() => void pause.setPaused(pause.enabled !== false)} />
      </header>
      {pause.enabled === false && <BotPausedBanner busy={pause.busy} onResume={() => void pause.setPaused(false)} />}
    </>
  )
}

/** Standard padded page container. */
export function PageBody({ children, wide }: { children: ReactNode; wide?: boolean }) {
  return <div className={cx('mx-auto w-full px-6 py-6 lg:px-8 lg:py-8', wide ? 'max-w-[1240px]' : 'max-w-[1040px]')}>{children}</div>
}
