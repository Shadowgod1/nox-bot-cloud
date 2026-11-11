import { useLayoutEffect, useRef, useState } from 'react'
import { ChevronRight, FlaskConical, RotateCcw, Send, Wrench } from 'lucide-react'
import { api } from '../../api/client'
import type { ChatTurn, TestChatResponse, ToolCallInfo } from '../../api/types'
import { Button, EmptyState, cx } from '../../components/ui'

interface Entry extends ChatTurn {
  tool_calls?: ToolCallInfo[]
  failed?: boolean
}

const SUGGESTIONS = ['Сколько стоит час?', 'Есть свободные места?', 'Хочу забронировать на завтра в 19:00 на троих', 'Где вы находитесь?']

export function TestChat() {
  const [entries, setEntries] = useState<Entry[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const logRef = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => {
    const el = logRef.current
    if (el && typeof el.scrollTo === 'function') el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [entries, busy])

  async function send(raw: string) {
    const t = raw.trim()
    if (!t || busy) return
    const history: Entry[] = [...entries.filter((e) => !e.failed), { role: 'user', content: t }]
    setEntries(history)
    setText('')
    setBusy(true)
    try {
      const res = await api.post<TestChatResponse>('/api/test-chat', {
        messages: history.map(({ role, content }) => ({ role, content })),
      })
      setEntries((e) => [...e, { role: 'assistant', content: res.reply, tool_calls: res.tool_calls ?? [] }])
    } catch (err) {
      setEntries((e) => [...e, { role: 'assistant', content: (err as Error).message || 'Бот не ответил', failed: true }])
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line bg-surface px-6 py-4 lg:px-8">
        <div>
          <h1 className="text-[18px] font-semibold text-ink">Тестовый чат</h1>
          <p className="text-[13px] text-ink-2">
            Переписка с ботом на текущих настройках, без WhatsApp. Сообщения не уходят клиентам и не попадают в «Диалоги».
          </p>
        </div>
        <Button size="sm" variant="ghost" disabled={!entries.length || busy} onClick={() => setEntries([])} icon={<RotateCcw className="size-3.5" aria-hidden />}>
          Начать заново
        </Button>
      </header>

      <div ref={logRef} role="log" aria-live="polite" aria-label="Тестовая переписка" className="min-h-0 flex-1 overflow-y-auto px-6 py-5 lg:px-8">
        <div className="mx-auto flex max-w-[760px] flex-col gap-3">
          {entries.length === 0 && (
            <EmptyState title="Напишите боту как клиент" icon={<FlaskConical className="size-5" />}>
              Проверьте, как бот отвечает на цены, свободные места и бронь. Под ответами видно, какие инструменты он вызывал.
            </EmptyState>
          )}
          {entries.map((e, i) =>
            e.role === 'user' ? (
              <div key={i} className="max-w-[80%] self-end rounded-[12px] rounded-tr-[4px] border border-line bg-surface px-3.5 py-2 text-sm whitespace-pre-wrap text-ink">
                {e.content}
              </div>
            ) : (
              <div key={i} className="flex max-w-[85%] flex-col gap-1.5 self-start">
                {e.tool_calls?.map((tc, j) => <ToolCallView key={j} call={tc} />)}
                <div
                  className={cx(
                    'rounded-[12px] rounded-tl-[4px] px-3.5 py-2 text-sm whitespace-pre-wrap',
                    e.failed ? 'border border-danger/30 bg-danger-soft text-danger' : 'bg-accent-soft text-ink',
                  )}
                >
                  {e.content}
                </div>
              </div>
            ),
          )}
          {busy && (
            <div className="self-start rounded-[12px] bg-accent-soft px-3.5 py-2.5" role="status" aria-label="Бот печатает">
              <span className="flex gap-1" aria-hidden>
                {[0, 1, 2].map((k) => (
                  <span key={k} className="size-1.5 animate-pulse rounded-full bg-accent" style={{ animationDelay: `${k * 160}ms` }} />
                ))}
              </span>
            </div>
          )}
        </div>
      </div>

      <form
        className="border-t border-line bg-surface px-6 py-3 lg:px-8"
        onSubmit={(e) => {
          e.preventDefault()
          void send(text)
        }}
      >
        <div className="mx-auto max-w-[760px]">
          {entries.length === 0 && (
            <div className="mb-2.5 flex flex-wrap gap-1.5">
              {SUGGESTIONS.map((s) => (
                <button key={s} type="button" onClick={() => void send(s)} className="rounded-full border border-line px-2.5 py-1 text-[13px] text-ink-2 hover:border-line-strong hover:text-ink">
                  {s}
                </button>
              ))}
            </div>
          )}
          <div className="flex items-end gap-2">
            <label htmlFor="test-input" className="sr-only">
              Сообщение боту
            </label>
            <textarea
              id="test-input"
              rows={1}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  void send(text)
                }
              }}
              placeholder="Сообщение от имени клиента"
              className="field-control max-h-32 min-h-[38px] resize-none"
            />
            <Button type="submit" variant="primary" loading={busy} disabled={!text.trim()} icon={<Send className="size-4" aria-hidden />}>
              Отправить
            </Button>
          </div>
        </div>
      </form>
    </div>
  )
}

function ToolCallView({ call }: { call: ToolCallInfo }) {
  const [open, setOpen] = useState(false)
  const hasError = Boolean(call.error)
  return (
    <div className={cx('rounded-[8px] border text-[13px]', hasError ? 'border-danger/30 bg-danger-soft' : 'border-line bg-sunken')}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-ink-2 hover:text-ink"
      >
        <ChevronRight className={cx('size-3.5 transition-transform', open && 'rotate-90')} aria-hidden />
        <Wrench className="size-3.5" aria-hidden />
        <span>
          Инструмент <code className="font-mono text-ink">{call.name}</code>
          {hasError && <span className="text-danger"> — ошибка</span>}
        </span>
      </button>
      {open && (
        <div className="grid gap-2 border-t border-line px-2.5 py-2">
          <div>
            <p className="mb-0.5 text-[12px] text-ink-3">Аргументы</p>
            <pre className="overflow-x-auto font-mono text-[12px] whitespace-pre-wrap text-ink">{JSON.stringify(call.args ?? {}, null, 2)}</pre>
          </div>
          <div>
            <p className="mb-0.5 text-[12px] text-ink-3">{hasError ? 'Ошибка' : 'Результат'}</p>
            <pre className="overflow-x-auto font-mono text-[12px] whitespace-pre-wrap text-ink">
              {hasError ? call.error : typeof call.result === 'string' ? call.result : JSON.stringify(call.result ?? null, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}
