import { useEffect, useState, type ReactNode } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router'
import {
  CalendarCheck,
  FlaskConical,
  ListChecks,
  LayoutDashboard,
  LogOut,
  Menu,
  MessagesSquare,
  Moon,
  PlugZap,
  Settings2,
  SlidersHorizontal,
  Sun,
  X,
  type LucideIcon,
} from 'lucide-react'
import { setTheme, useBrand, useTheme } from '../brand'
import { useAuth } from '../hooks/useAuth'
import { ru } from '../i18n/ru'
import { StatusBanner } from './StatusBanner'
import { BotPausedBanner, BotPauseSwitch, useBotPause } from './BotPause'
import { cx } from './ui'

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
  { to: '/knowledge', label: ru.nav.knowledge, icon: ListChecks },
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
        'app-nav-link flex h-9 w-full items-center gap-2.5 rounded-[7px] px-2.5 text-sm text-ink-2 hover:bg-sunken hover:text-ink md:max-lg:justify-center',
        className,
      )}
    >
      {dark ? <Sun className="size-[18px] shrink-0 text-ink-3" aria-hidden /> : <Moon className="size-[18px] shrink-0 text-ink-3" aria-hidden />}
      <span className="md:max-lg:sr-only">{label}</span>
    </button>
  )
}

/**
 * App shell. The work area always takes the whole width next to the sidebar:
 * phones (< md) get a drawer behind the menu button, tablets (md..lg) an icon rail, desktops the full sidebar.
 */
export function Layout() {
  const { user, logout } = useAuth()
  const brand = useBrand()
  const [navOpen, setNavOpen] = useState(false)
  const { pathname } = useLocation()

  // The drawer closes after navigation and on Escape.
  useEffect(() => setNavOpen(false), [pathname])
  useEffect(() => {
    if (!navOpen) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setNavOpen(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navOpen])

  return (
    <div className="flex h-dvh min-h-0">
      {navOpen && <div aria-hidden className="fixed inset-0 z-30 bg-black/40 md:hidden" onClick={() => setNavOpen(false)} />}
      <aside
        id="app-nav"
        className={cx(
          'app-sidebar flex shrink-0 flex-col border-r border-line bg-canvas md:w-[64px] lg:w-[232px]',
          // phones: off-canvas drawer, invisible while closed so its links leave the tab order
          'max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-40 max-md:w-[272px] max-md:shadow-[var(--shadow-pop)] max-md:transition-[translate,visibility] max-md:duration-200 max-md:ease-out',
          navOpen ? 'max-md:translate-x-0' : 'max-md:invisible max-md:-translate-x-full',
        )}
        aria-label="Основная навигация"
      >
        <div className="flex h-14 shrink-0 items-center px-5 md:max-lg:justify-center md:max-lg:px-0">
          <Wordmark className="text-[17px] md:max-lg:text-[13px] md:max-lg:tracking-[0.08em]" />
          <span className="ml-2 text-[12px] text-ink-3 md:max-lg:hidden">админка</span>
          <button
            type="button"
            onClick={() => setNavOpen(false)}
            aria-label="Закрыть меню"
            className="-mr-2 ml-auto grid size-9 place-items-center rounded-[7px] text-ink-2 hover:bg-sunken md:hidden"
          >
            <X className="size-[18px]" aria-hidden />
          </button>
        </div>
        <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto px-2.5 py-2">
          {brand.id === 'nox' && (
            <p className="app-nav-section mb-1 px-2.5 text-[11px] font-bold tracking-[0.08em] uppercase md:max-lg:hidden">Меню</p>
          )}
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              title={item.label}
              className={({ isActive }) =>
                cx(
                  'app-nav-link group flex h-9 items-center gap-2.5 rounded-[7px] px-2.5 text-sm transition-colors duration-100 md:max-lg:justify-center',
                  isActive ? 'bg-surface font-medium text-ink shadow-[0_1px_2px_oklch(25%_0.02_230/0.10)] ring-1 ring-line' : 'text-ink-2 hover:bg-sunken hover:text-ink',
                )
              }
            >
              {({ isActive }) => (
                <>
                  <item.icon className={cx('size-[18px] shrink-0', isActive ? 'text-accent' : 'text-ink-3 group-hover:text-ink-2')} aria-hidden />
                  <span className="truncate md:max-lg:sr-only">{item.label}</span>
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-line p-2.5">
          {user && (
            <div className="mb-1 px-2.5 py-1.5 md:max-lg:hidden">
              <p className="truncate text-[13px] font-medium text-ink">{user.name || user.email}</p>
              <p className="truncate text-[12px] text-ink-3">{ru.roles[user.role]}</p>
            </div>
          )}
          <ThemeToggle />
          <button
            type="button"
            onClick={() => void logout()}
            title={ru.common.logout}
            className="app-nav-link flex h-9 w-full items-center gap-2.5 rounded-[7px] px-2.5 text-sm text-ink-2 hover:bg-sunken hover:text-ink md:max-lg:justify-center"
          >
            <LogOut className="size-[18px] shrink-0 text-ink-3" aria-hidden />
            <span className="md:max-lg:sr-only">{ru.common.logout}</span>
          </button>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar navOpen={navOpen} onMenu={() => setNavOpen(true)} />
        <StatusBanner />
        <main className="min-h-0 flex-1 overflow-y-auto" id="main">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

/** Thin header over every page: the menu button on phones, the global "bot on pause" switch, plus a banner while paused. */
function TopBar({ navOpen, onMenu }: { navOpen: boolean; onMenu: () => void }) {
  const pause = useBotPause()
  const brand = useBrand()
  return (
    <>
      <header className="flex h-12 shrink-0 items-center justify-end gap-3 border-b border-line bg-surface px-4 sm:px-6 lg:px-8">
        <button
          type="button"
          onClick={onMenu}
          aria-label="Меню"
          aria-expanded={navOpen}
          aria-controls="app-nav"
          className="-ml-2 grid size-9 place-items-center rounded-[7px] text-ink-2 hover:bg-sunken md:hidden"
        >
          <Menu className="size-5" aria-hidden />
        </button>
        {/* decorative: the labelled wordmark lives in the sidebar */}
        <span aria-hidden className="mr-auto inline-flex items-center gap-2 text-[15px] font-bold text-ink select-none md:hidden">
          {brand.id === 'nox' && <img src={brand.favicon} alt="" className="size-5" />}
          <span className={brand.id === 'nox' ? undefined : 'font-semibold tracking-[0.14em]'}>{brand.name}</span>
        </span>
        <BotPauseSwitch enabled={pause.enabled} busy={pause.busy} onToggle={() => void pause.setPaused(pause.enabled !== false)} />
      </header>
      {pause.enabled === false && <BotPausedBanner busy={pause.busy} onResume={() => void pause.setPaused(false)} />}
    </>
  )
}

/**
 * Standard padded page container. It spans the whole work area: no centred column. Pages lay out their
 * own grids inside, and only reading text (descriptions, the prompt) gets a line-length limit.
 * The 2400px cap only matters on ultra-wide monitors.
 */
export function PageBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx('mx-auto w-full max-w-[2400px] px-4 py-5 sm:px-6 sm:py-6 lg:px-8 lg:py-8', className)}>{children}</div>
}
