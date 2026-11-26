import { Check } from 'lucide-react'
import type { Status } from '../api'
import { cx } from './ui'

/** Where an application is on the way to a working bot. null: the status is outside that path. */
export function stepsFor(status: Status, free: boolean): { labels: string[]; current: number } | null {
  const labels = free
    ? ['Заявка', 'Тариф', 'Создание бота', 'Бот работает']
    : ['Заявка', 'Тариф', 'Оплата', 'Создание бота', 'Бот работает']
  const at = (label: string) => labels.indexOf(label)
  switch (status) {
    case 'verified':
      return { labels, current: at('Тариф') }
    case 'awaiting_invoice':
    case 'awaiting_payment':
    case 'payment_pending':
      return { labels, current: at('Оплата') }
    case 'approved':
    case 'provisioning':
    case 'failed':
      return { labels, current: at('Создание бота') }
    case 'active':
      return { labels, current: labels.length }
    default:
      return null
  }
}

export function Steps({ labels, current }: { labels: string[]; current: number }) {
  const done = current >= labels.length
  return (
    <nav aria-label="Этапы подключения">
      <p className="mb-2 text-[13px] font-semibold text-ink-2 sm:hidden">
        {done ? 'Все этапы пройдены' : `Шаг ${current + 1} из ${labels.length}: ${labels[current]}`}
      </p>
      <ol className="flex items-center gap-1.5 sm:gap-0">
        {labels.map((label, i) => {
          const state = i < current ? 'done' : i === current ? 'current' : 'todo'
          return (
            <li
              key={label}
              aria-current={state === 'current' ? 'step' : undefined}
              className={cx('flex min-w-0 flex-1 items-center', i === labels.length - 1 && 'sm:flex-none')}
            >
              {/* phones: a segmented bar */}
              <span
                className={cx(
                  'h-1.5 w-full rounded-full sm:hidden',
                  state === 'done' ? 'bg-ok' : state === 'current' ? 'bg-accent' : 'bg-line-strong',
                )}
              />
              {/* wider: dot + label + connector */}
              <span className="hidden min-w-0 items-center gap-2 sm:flex">
                <span
                  className={cx(
                    'grid size-6 flex-none place-items-center rounded-full text-[12px] font-bold tabular-nums',
                    state === 'done' && 'bg-ok text-white',
                    state === 'current' && 'bg-accent text-white ring-4 ring-accent-soft',
                    state === 'todo' && 'border border-line-strong bg-raised text-ink-3',
                  )}
                >
                  {state === 'done' ? <Check className="size-3.5" strokeWidth={3} aria-hidden /> : i + 1}
                </span>
                <span
                  className={cx(
                    'truncate text-[13.5px]',
                    state === 'current' ? 'font-bold text-ink' : state === 'done' ? 'font-semibold text-ink-2' : 'text-ink-3',
                  )}
                >
                  {label}
                  <span className="sr-only">{state === 'done' ? ' — пройдено' : state === 'current' ? ' — текущий этап' : ''}</span>
                </span>
              </span>
              {i < labels.length - 1 && (
                <span aria-hidden className={cx('mx-3 hidden h-px min-w-4 flex-1 sm:block', i < current ? 'bg-ok' : 'bg-line-strong')} />
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
