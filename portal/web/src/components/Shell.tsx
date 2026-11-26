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

const STEPS: [string, string][] = [
  ['Заявка', 'Компания, контакты и адрес бота. Подтверждаете e-mail кодом из письма.'],
  ['Тариф или бесплатный тест', 'Тест подключается сразу и без оплаты. Платный тариф оплачивается по счёту.'],
  ['Бот на вашем адресе', 'Панель на имя.noxsw.kz: подключаете номер WhatsApp, вносите услуги и цены — бот отвечает клиентам.'],
]

/** What the visitor gets: shown beside the auth form on wide screens. */
function AuthAside() {
  return (
    <aside className="hidden lg:block">
      <h2 className="max-w-[24ch] text-[30px] leading-[1.15] font-extrabold tracking-[-0.02em] text-ink">
        ИИ-ассистент для WhatsApp, который отвечает вашим клиентам
      </h2>
      <p className="mt-3 max-w-[46ch] text-[15px] text-ink-2">
        Цены, запись, адрес и график — бот отвечает сам, сложные вопросы передаёт вашим операторам.
      </p>
      <ol className="mt-8 flex flex-col gap-5">
        {STEPS.map(([title, text], i) => (
          <li key={title} className="flex gap-3.5">
            <span className="grid size-7 flex-none place-items-center rounded-full border border-line-strong bg-surface text-[13px] font-bold text-ink-2 tabular-nums">
              {i + 1}
            </span>
            <span className="pt-0.5">
              <span className="block text-[15px] font-bold text-ink">{title}</span>
              <span className="mt-0.5 block max-w-[44ch] text-[14px] text-ink-3">{text}</span>
            </span>
          </li>
        ))}
      </ol>
      <p className="mt-8 text-[13.5px] text-ink-3">
        Вопросы —{' '}
        <a href="mailto:info@noxsw.kz" className="font-semibold text-ink-2 hover:text-accent">
          info@noxsw.kz
        </a>
      </p>
    </aside>
  )
}

/** Auth pages: the form card, with the "what you get" panel beside it on wide screens. */
export function AuthCard({ title, lead, children, aside = true }: { title: string; lead?: ReactNode; children: ReactNode; aside?: boolean }) {
  return (
    <Shell wide>
      <div
        className={cx(
          'mx-auto grid w-full max-w-[460px] items-center gap-12 lg:py-6',
          aside && 'lg:max-w-none lg:grid-cols-[minmax(0,1fr)_460px]',
        )}
      >
        {aside && <AuthAside />}
        <div className="rounded-nx border border-line bg-surface p-6 shadow-[0_1px_2px_rgb(0_0_0/0.04),0_12px_32px_-18px_rgb(0_0_0/0.25)] sm:p-8">
          <h1 className="text-[22px] font-extrabold tracking-[-0.015em] text-ink">{title}</h1>
          {lead && <div className="mt-1.5 text-[14.5px] text-ink-2">{lead}</div>}
          <div className="mt-6">{children}</div>
        </div>
      </div>
    </Shell>
  )
}
