import { useId, type ReactNode } from 'react'
import { Link } from 'react-router'
import { LogOut, MessageCircle, Moon, Sun } from 'lucide-react'
import { useTheme } from '../theme'
import { SupportLinks, usePublicConfig } from './Support'
import { cx } from './ui'
import { Backdrop } from './Backdrop'

/** The living bot mark of the admin panel (blinks, glances around, the antenna glows noxsw lime), in portal violet. */
export function BotMark() {
  const gradient = useId()
  return (
    <svg aria-hidden viewBox="0 0 32 32" className="size-8 shrink-0" data-testid="bot-mark">
      <defs>
        <linearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" style={{ stopColor: 'color-mix(in oklab, var(--nx-violet) 78%, white)' }} />
          <stop offset="1" style={{ stopColor: 'var(--nx-violet)' }} />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill={`url(#${gradient})`} />
      <line x1="16" y1="10" x2="16" y2="6.6" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" />
      <circle className="nox-antenna" cx="16" cy="5.6" r="2" fill="#c6ff3d" />
      <rect x="6" y="10" width="20" height="15" rx="6" fill="#fff" />
      <g className="nox-eyes fill-violet">
        <rect className="nox-eye" x="11" y="14.5" width="3" height="5.5" rx="1.5" />
        <rect className="nox-eye" x="18" y="14.5" width="3" height="5.5" rx="1.5" />
      </g>
    </svg>
  )
}

export function Wordmark({ to = '/', subtitle }: { to?: string; subtitle?: string }) {
  return (
    <Link to={to} aria-label="Nox Bot — на главную" className="nox-wordmark flex items-center gap-2.5 text-ink no-underline select-none">
      <BotMark />
      <span className="font-display text-[20px] leading-none font-extrabold tracking-[-0.01em] whitespace-nowrap text-ink">NOX SW</span>
      {subtitle && (
        <span className="ml-0.5 hidden border-l border-line-strong pl-3 text-[13px] leading-none text-ink-3 sm:inline">{subtitle}</span>
      )}
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
  /** auth pages: the noxsw.kz look with the binary rain; without it — the calm, motionless app theme */
  backdrop?: boolean
}

/** Top bar + centered content. */
export function Shell({ children, subtitle, home = '/', user, onLogout, wide, backdrop = false }: ShellProps) {
  const support = usePublicConfig()?.support
  return (
    <div className={cx('relative isolate flex min-h-dvh flex-col', !backdrop && 'nx-app')}>
      {backdrop && <Backdrop />}
      <header className="sticky top-0 z-10 border-b border-line bg-surface/85 backdrop-blur-md">
        <div className={cx('mx-auto flex h-14 items-center gap-3 px-4', wide ? 'max-w-[1180px]' : 'max-w-[880px]')}>
          <Wordmark to={home} subtitle={subtitle} />
          <div className="ml-auto flex items-center gap-1">
            {user && <span className="mr-2 hidden text-[13px] text-ink-3 sm:inline">{user}</span>}
            {support?.whatsapp_url && (
              <a
                href={support.whatsapp_url}
                target="_blank"
                rel="noopener noreferrer"
                aria-label="Написать в поддержку в WhatsApp"
                title="Поддержка в WhatsApp"
                className="flex h-9 items-center gap-1.5 rounded-nx px-2.5 text-[13.5px] font-semibold text-ink-2 hover:bg-sunken hover:text-ink"
              >
                <MessageCircle className="size-[18px]" aria-hidden />
                <span className="hidden sm:inline">Поддержка</span>
              </a>
            )}
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
      <footer className="flex flex-col items-center gap-2 px-4 pb-6 text-center text-[12.5px] text-ink-3">
        <SupportLinks support={support} className="justify-center text-[13px]" />
        <nav aria-label="Документы" className="flex flex-wrap justify-center gap-x-4 gap-y-1">
          <Link to="/offer" className="text-ink-2 hover:text-accent">
            Оферта
          </Link>
          <Link to="/refund" className="text-ink-2 hover:text-accent">
            Возврат денег
          </Link>
          <Link to="/privacy" className="text-ink-2 hover:text-accent">
            Конфиденциальность
          </Link>
        </nav>
        <span>
          Nox service · Усть-Каменогорск ·{' '}
          <a href="https://noxsw.kz" className="text-ink-2 hover:text-accent" rel="noopener">
            noxsw.kz
          </a>
        </span>
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
  const support = usePublicConfig()?.support
  return (
    <aside className="relative hidden lg:block">
      {/* a soft dark pool under the text so the binary rain never fights the copy */}
      <div aria-hidden className="pointer-events-none absolute -inset-x-16 -inset-y-12 -z-10 rounded-[48px] bg-[radial-gradient(ellipse_at_40%_45%,var(--color-canvas)_35%,transparent_75%)] opacity-90" />
      <span className="inline-flex items-center gap-2 rounded-full border border-line-strong bg-surface/70 px-3 py-1 font-mono text-[11px] font-bold tracking-[0.08em] text-ink-2 uppercase">
        <span className="size-1.5 rounded-full bg-accent" aria-hidden />
        ИИ-бот для WhatsApp
      </span>
      <h2 className="mt-5 max-w-[16ch] font-display text-[40px] leading-[1.02] font-extrabold tracking-[-0.025em] text-ink uppercase xl:text-[46px]">
        Отвечает вашим клиентам <span className="nx-iridescent">за вас</span>
      </h2>
      <p className="mt-3 max-w-[46ch] text-[15px] text-ink-2">
        Цены, запись, адрес и график — бот отвечает сам, сложные вопросы передаёт вашим операторам.
      </p>
      <ol className="mt-8 flex flex-col gap-5">
        {STEPS.map(([title, text], i) => (
          <li key={title} className="flex gap-3.5">
            <span className="grid size-7 flex-none place-items-center rounded-nx border border-line-strong bg-surface/70 font-mono text-[12px] font-bold text-accent-ink tabular-nums">
              {String(i + 1).padStart(2, '0')}
            </span>
            <span className="pt-0.5">
              <span className="block text-[15px] font-bold text-ink">{title}</span>
              <span className="mt-0.5 block max-w-[44ch] text-[14px] text-ink-3">{text}</span>
            </span>
          </li>
        ))}
      </ol>
      <div className="mt-8 text-[13.5px] text-ink-3">
        <p>Есть вопросы — позвоните или напишите, поможем:</p>
        {support ? (
          <SupportLinks support={support} email className="mt-2" />
        ) : (
          <a href="mailto:info@noxsw.kz" className="mt-2 inline-block font-semibold text-ink-2 hover:text-accent">
            info@noxsw.kz
          </a>
        )}
      </div>
    </aside>
  )
}

/** Phones: the aside is hidden, so the card itself ends with the support contacts (when configured). */
function AuthHelp() {
  const support = usePublicConfig()?.support
  if (!support || !(support.phone || support.whatsapp_url)) return null
  return (
    <div className="mt-6 border-t border-line pt-4 text-[13px] text-ink-3 lg:hidden">
      <p>Нужна помощь с заявкой?</p>
      <SupportLinks support={support} className="mt-1.5" />
    </div>
  )
}

/** Auth pages: the form card, with the "what you get" panel beside it on wide screens. */
export function AuthCard({ title, lead, children, aside = true }: { title: string; lead?: ReactNode; children: ReactNode; aside?: boolean }) {
  return (
    <Shell wide backdrop>
      <div
        className={cx(
          'mx-auto grid w-full max-w-[460px] items-center gap-12 lg:py-6',
          aside && 'lg:max-w-none lg:grid-cols-[minmax(0,1fr)_460px]',
        )}
      >
        {aside && <AuthAside />}
        <div className="nx-panel p-6 sm:p-8">
          <h1 className="font-display text-[24px] leading-tight font-extrabold tracking-[-0.015em] text-ink">{title}</h1>
          {lead && <div className="mt-1.5 text-[14.5px] text-ink-2">{lead}</div>}
          <div className="mt-6">{children}</div>
          {aside && <AuthHelp />}
        </div>
      </div>
    </Shell>
  )
}
