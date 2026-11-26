import { useEffect, useState } from 'react'
import { Check, Loader2 } from 'lucide-react'
import { cx } from './ui'

/** Typical time from approval to a working bot (queue + start + address), seconds. */
const EXPECTED_S = 45
const KEY = 'nox-build-started'

function startedAt(): number {
  try {
    const saved = Number(sessionStorage.getItem(KEY))
    if (saved && Date.now() - saved < 30 * 60_000) return saved
    const now = Date.now()
    sessionStorage.setItem(KEY, String(now))
    return now
  } catch {
    return Date.now()
  }
}

export function forgetBuildStart() {
  try {
    sessionStorage.removeItem(KEY)
  } catch {
    /* private mode */
  }
}

/**
 * What the client sees while the bot is being created: a progress bar and the stages, so it is obvious that
 * something is happening. ``queued`` = approved, waiting for the server (status approved); otherwise the
 * server is building it (status provisioning). The bar follows the typical time and stops at 95 % until the
 * cabinet reloads with the ready bot.
 */
export function BuildProgress({ queued, botHost }: { queued: boolean; botHost: string }) {
  const [start] = useState(startedAt)
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(t)
  }, [])
  const elapsed = Math.max(0, (now - start) / 1000)
  // eases toward 95 %: fast at first, never "done" before the bot really is
  const pct = Math.min(95, Math.round(95 * (1 - Math.exp(-elapsed / (EXPECTED_S / 2.2)))))
  const stage = queued ? 0 : elapsed < 12 ? 1 : elapsed < 30 ? 2 : 3
  const stages = [
    'Заявка одобрена, бот в очереди на создание',
    'Запускаем вашего бота на сервере',
    `Подключаем адрес ${botHost}`,
    'Проверяем, что всё работает',
  ]
  const slow = elapsed > 150

  return (
    <div className="mt-5" role="status" aria-live="polite">
      <div className="flex items-baseline justify-between gap-3 text-[13px]">
        <span className="font-semibold text-ink">{stages[stage]}…</span>
        <span className="text-ink-3 tabular-nums">{pct}%</span>
      </div>
      <div
        className="mt-2 h-2 overflow-hidden rounded-full bg-sunken"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-label="Создание бота"
      >
        <div
          className="h-full rounded-full bg-accent transition-[width] duration-500 ease-out"
          style={{ width: `${Math.max(pct, 3)}%` }}
        />
      </div>
      <ol className="mt-4 flex flex-col gap-2">
        {stages.map((label, i) => (
          <li key={label} className={cx('flex items-center gap-2.5 text-[13.5px]', i > stage ? 'text-ink-3' : 'text-ink')}>
            <span
              className={cx(
                'grid size-5 flex-none place-items-center rounded-full',
                i < stage && 'bg-ok text-white',
                i === stage && 'text-accent',
                i > stage && 'border border-line-strong',
              )}
              aria-hidden
            >
              {i < stage ? <Check className="size-3" strokeWidth={3} /> : i === stage ? <Loader2 className="size-4 animate-spin" /> : null}
            </span>
            {label}
          </li>
        ))}
      </ol>
      <p className="mt-4 text-[13px] text-ink-3">
        {slow
          ? 'Дольше обычного. Как только бот будет готов, страница обновится сама и придёт письмо. Если через 5 минут ничего не изменится — напишите в поддержку.'
          : 'Обычно меньше минуты. Страница обновится сама, ничего нажимать не нужно.'}
      </p>
    </div>
  )
}
