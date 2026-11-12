import { useEffect, useState, type ReactNode } from 'react'
import type { DashboardHealth } from '../../api/types'
import { cx } from '../../components/ui'
import { plural } from '../../i18n/ru'
import { formatAgo, formatSeconds, type BotStatus, type Level } from './health'

const frame: Record<Level, string> = {
  ok: 'border-ok/25 bg-ok-soft',
  warn: 'border-warn/30 bg-warn-soft',
  danger: 'border-danger/30 bg-danger-soft',
}
const dot: Record<Level, string> = { ok: 'bg-ok', warn: 'bg-warn', danger: 'bg-danger' }
const halo: Record<Level, string> = { ok: 'bg-ok/20', warn: 'bg-warn/20', danger: 'bg-danger/20' }
const titleTone: Record<Level, string> = { ok: 'text-ink', warn: 'text-ink', danger: 'text-danger' }

/** Re-render every minute so "N минут назад" stays true without a refetch. */
function useNow(intervalMs = 60_000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(id)
  }, [intervalMs])
  return now
}

export function StatusHero({ status, health, action }: { status: BotStatus; health: DashboardHealth; action?: ReactNode }) {
  const now = useNow()
  const share = health.answered_share_today
  const handovers = health.handovers_today

  return (
    <section
      aria-labelledby="bot-status-h"
      data-level={status.level}
      className={cx('overflow-hidden rounded-[var(--radius-panel)] border', frame[status.level])}
    >
      <div className="flex flex-col gap-3 px-5 py-5 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-start gap-3.5">
          <span aria-hidden className="relative mt-1 grid size-5 shrink-0 place-items-center">
            <span className={cx('absolute inset-0 rounded-full', halo[status.level])} />
            <span className={cx('relative size-2.5 rounded-full', dot[status.level])} />
          </span>
          <div className="min-w-0">
            <h2 id="bot-status-h" className={cx('text-[19px] font-semibold leading-snug', titleTone[status.level])}>
              {status.title}
            </h2>
            {status.reasons.length > 0 && (
              <ul className="mt-1.5 space-y-0.5 text-sm text-ink-2">
                {status.reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            )}
          </div>
        </div>
        {action && <div className="shrink-0 pl-[34px] sm:pl-0">{action}</div>}
      </div>

      <dl className="grid grid-cols-2 gap-px border-t border-line bg-line sm:grid-cols-4">
        <Metric
          label="Последний ответ бота"
          value={health.last_bot_reply_at ? formatAgo(health.last_bot_reply_at, now) : 'ещё не было'}
        />
        <Metric
          label="Среднее время ответа"
          value={health.avg_reply_ms_today != null ? formatSeconds(health.avg_reply_ms_today) : '—'}
          hint="сегодня"
        />
        <Metric
          label="Бот ответил сам"
          value={share != null ? `${Math.round(share * 100)}% диалогов` : '—'}
          hint="сегодня"
        />
        <Metric
          label="Передано оператору"
          value={`${handovers} ${plural(handovers, ['диалог', 'диалога', 'диалогов'])}`}
          hint="сегодня"
        />
      </dl>
    </section>
  )
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex flex-col-reverse bg-surface px-5 py-3.5">
      <dt className="mt-0.5 text-[13px] text-ink-2">
        {label}
        {hint && <span className="text-ink-3"> · {hint}</span>}
      </dt>
      <dd className="tabular text-[15px] font-semibold text-ink">{value}</dd>
    </div>
  )
}
