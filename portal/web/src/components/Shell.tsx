import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { LogOut, Moon, Sun } from 'lucide-react'
import { useTheme } from '../theme'
import { cx } from './ui'

export function Wordmark({ to = '/', subtitle }: { to?: string; subtitle?: string }) {
  return (
    <Link to={to} className="flex items-center gap-2.5 text-ink no-underline">
      <span className="grid size-8 place-items-center rounded-nx bg-accent text-[15px] font-extrabold text-white">N</span>
      <span className="leading-tight">
        <span className="block text-[16px] font-extrabold tracking-tight">Nox Bot</span>
        {subtitle && <span className="block text-[12px] text-ink-3">{subtitle}</span>}
      </span>
    </Link>
  )
}

export function ThemeToggle() {
  const [theme, toggle] = useTheme()
  const dark = theme === 'dark'
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={dark ? 'Светлая тема' : 'Тёмная тема'}
      title={dark ? 'Светлая тема' : 'Тёмная тема'}
      className="grid size-9 place-items-center rounded-nx text-ink-2 hover:bg-sunken hover:text-ink"
    >
      {dark ? <Sun className="size-[18px]" /> : <Moon className="size-[18px]" />}
    </button>
  )
}

interface ShellProps {
  children: ReactNode
  subtitle?: string
  home?: string
  user?: string
  onLogout?: () => void
  wide?: boolean
}

/** Top bar + centered content. */
export function Shell({ children, subtitle, home = '/', user, onLogout, wide }: ShellProps) {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="border-b border-line bg-surface">
        <div className={cx('mx-auto flex h-14 items-center gap-3 px-4', wide ? 'max-w-[1180px]' : 'max-w-[880px]')}>
          <Wordmark to={home} subtitle={subtitle} />
          <div className="ml-auto flex items-center gap-1">
            {user && <span className="mr-2 hidden text-[13px] text-ink-3 sm:inline">{user}</span>}
            <ThemeToggle />
            {onLogout && (
              <button
                type="button"
                onClick={onLogout}
                className="flex h-9 items-center gap-1.5 rounded-nx px-2.5 text-[13.5px] font-semibold text-ink-2 hover:bg-sunken hover:text-ink"
              >
                <LogOut className="size-4" aria-hidden />
                Выйти
              </button>
            )}
          </div>
        </div>
      </header>
      <main className={cx('mx-auto w-full flex-1 px-4 py-8', wide ? 'max-w-[1180px]' : 'max-w-[880px]')}>{children}</main>
      <footer className="px-4 pb-6 text-center text-[12.5px] text-ink-3">
        Nox service · Усть-Каменогорск ·{' '}
        <a href="https://noxsw.kz" className="text-ink-2 hover:text-accent" rel="noopener">
          noxsw.kz
        </a>
      </footer>
    </div>
  )
}

/** Narrow centered card for auth pages. */
export function AuthCard({ title, lead, children }: { title: string; lead?: ReactNode; children: ReactNode }) {
  return (
    <Shell>
      <div className="mx-auto w-full max-w-[460px]">
        <div className="rounded-nx border border-line bg-surface p-6 sm:p-7">
          <h1 className="text-[21px] font-extrabold tracking-tight text-ink">{title}</h1>
          {lead && <div className="mt-1.5 text-[14px] text-ink-2">{lead}</div>}
          <div className="mt-6">{children}</div>
        </div>
      </div>
    </Shell>
  )
}
