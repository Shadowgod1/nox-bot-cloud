import { useEffect, useState } from 'react'
import { Pause, Play } from 'lucide-react'
import { api } from '../api/client'
import type { BotPauseState } from '../api/types'
import { useEvent } from '../hooks/useEvents'
import { toast } from './toast'
import { cx } from './ui'

/** Shared state of the global "bot on pause" switch (header + banner). */
export function useBotPause() {
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let alive = true
    api
      .get<BotPauseState>('/api/settings/bot/pause', { silent: true })
      .then((s) => alive && setEnabled(s.enabled))
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [])

  useEvent('bot.status', (s) => setEnabled(s.enabled))

  async function setPaused(paused: boolean) {
    setBusy(true)
    try {
      const s = await api.post<BotPauseState>('/api/settings/bot/pause', { paused })
      setEnabled(s.enabled)
      toast.success(s.enabled ? 'Бот снова отвечает клиентам' : 'Бот на паузе: клиентам отвечаете вы')
    } catch {
      /* toast */
    } finally {
      setBusy(false)
    }
  }

  return { enabled, busy, setPaused }
}

/** Compact switch for the header: green "Бот отвечает" / amber "Бот на паузе". */
export function BotPauseSwitch({ enabled, busy, onToggle }: { enabled: boolean | null; busy: boolean; onToggle: () => void }) {
  if (enabled === null) return null
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      aria-label={enabled ? 'Бот отвечает клиентам. Нажмите, чтобы поставить на паузу' : 'Бот на паузе. Нажмите, чтобы включить'}
      onClick={onToggle}
      disabled={busy}
      className={cx(
        'inline-flex h-8 items-center gap-2 rounded-full border pr-3 pl-1 text-[13px] font-medium transition-colors disabled:opacity-60',
        enabled ? 'border-ok/30 bg-ok-soft text-ink hover:border-ok/60' : 'border-warn/50 bg-warn-soft text-ink hover:border-warn',
      )}
    >
      <span
        aria-hidden
        className={cx('relative h-6 w-10 shrink-0 rounded-full transition-colors', enabled ? 'bg-ok' : 'bg-line-strong')}
      >
        <span
          className={cx(
            'absolute top-0.5 grid size-5 place-items-center rounded-full bg-white text-ink-2 shadow transition-[left] duration-150',
            enabled ? 'left-[18px]' : 'left-0.5',
          )}
        >
          {enabled ? <Play className="size-2.5" /> : <Pause className="size-2.5" />}
        </span>
      </span>
      {enabled ? 'Бот отвечает' : 'Бот на паузе'}
    </button>
  )
}

/** Banner under the header while the bot is paused: hard to miss, one click to resume. */
export function BotPausedBanner({ onResume, busy }: { onResume: () => void; busy: boolean }) {
  return (
    <div role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-warn/40 bg-warn-soft px-4 py-2.5 text-sm text-ink sm:px-6 lg:px-8">
      <Pause className="size-4 shrink-0 text-warn" aria-hidden />
      <span className="font-medium">Бот на паузе — клиентам никто не отвечает автоматически.</span>
      <span className="text-ink-2">Сообщения сохраняются в «Диалогах».</span>
      <button type="button" onClick={onResume} disabled={busy} className="font-medium text-accent underline disabled:opacity-60">
        Включить бота
      </button>
    </div>
  )
}
