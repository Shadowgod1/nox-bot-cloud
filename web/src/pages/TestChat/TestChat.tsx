import { useLayoutEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { ChevronRight, FlaskConical, Image as ImageIcon, Mic, RotateCcw, Send, UserRoundCheck, Wrench } from 'lucide-react'
import { api } from '../../api/client'
import type { BotSettings, ChatTurn, TestChatResponse, TestMedia, ToolCallInfo } from '../../api/types'
import { useLoad } from '../../hooks/useLoad'
import { Button, EmptyState, cx } from '../../components/ui'
import { coinsWord } from '../../components/PriceImport'

interface Entry extends ChatTurn {
  tool_calls?: ToolCallInfo[]
  failed?: boolean
  handed_over?: boolean
  coins?: number | null
}

const SUGGESTIONS: Record<string, string[]> = {
  beauty_salon: ['Сколько стоит маникюр?', 'Можно к Айгерим в четверг после 6?', 'Где вы находитесь?', 'Маникюр қанша тұрады?'],
  clinic: ['Сколько стоит чистка зубов?', 'Можно к стоматологу в субботу утром?', 'Где вы находитесь?', 'Тіс емдеу қанша тұрады?'],
  computer_club: ['Сколько стоит час?', 'Есть свободные места?', 'Хочу забронировать на завтра в 19:00 на троих', 'Где вы находитесь?'],
}
const DEFAULT_SUGGESTIONS = ['Сколько стоят ваши услуги?', 'Можно подъехать завтра в 15:00?', 'Где вы находитесь?', 'Қандай қызметтер бар?']
/** core/iler/api/test_chat.py: one message up to 4000 characters, a dialogue up to 50 messages. */
export const MAX_MESSAGE = 4000
const MAX_TURNS = 50
const MEDIA_LABEL: Record<TestMedia, string> = { audio: 'Голосовое сообщение', image: 'Фото' }

/** A test-chat entry as core expects it. Bot replies echo their tool calls (without results), so the client
 * card of the test dialogue knows about a booking or a fact from an earlier reply; a failed call is marked. */
export function toTurn({ role, content, media, tool_calls }: Entry): ChatTurn {
  const text = content.length > MAX_MESSAGE ? content.slice(0, MAX_MESSAGE) : content  // a very long bot reply
  const turn: ChatTurn = media ? { role, content: text, media } : { role, content: text }
  if (role === 'assistant' && tool_calls?.length) {
    turn.tool_calls = tool_calls.slice(0, 8).map((c) => ({
      name: c.name,
      args: c.args,
      error: c.error ?? (typeof c.result === 'string' && c.result.startsWith('error') ? c.result : null),
    }))
  }
  return turn
}

export function formatCoins(n: number): string {
  const v = n < 10 ? Math.round(n * 100) / 100 : Math.round(n * 10) / 10
  return v.toLocaleString('ru-RU')
}

/**
 * Test chat: the owner writes as a client and sees the bot's replies on the current settings, before
 * WhatsApp is connected. Nothing goes to clients or to "Диалоги". `embedded` = inside the setup wizard.
 */
export function TestChat({ embedded = false }: { embedded?: boolean }) {
  const bot = useLoad(() => api.get<BotSettings>('/api/settings/bot', { silent: true }))
  const [entries, setEntries] = useState<Entry[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const logRef = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => {
    const el = logRef.current
    if (el && typeof el.scrollTo === 'function') el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [entries, busy])

  const suggestions = SUGGESTIONS[bot.data?.template_id ?? ''] ?? DEFAULT_SUGGESTIONS
  const spent = entries.reduce((sum, e) => sum + (e.coins ?? 0), 0)
  const notConfigured = bot.data ? !bot.data.prompt.trim() : false

  async function send(raw: string, media?: TestMedia) {
    const t = raw.trim()
    if ((!t && !media) || busy || t.length > MAX_MESSAGE) return
    const turn: Entry = media ? { role: 'user', content: t, media } : { role: 'user', content: t }
    // failed exchanges stay on screen but never go back to core: one bad message must not break the chat
    const history: Entry[] = [...entries.filter((e) => !e.failed), turn]
    setEntries((e) => [...e, turn])
    setText('')
    setBusy(true)
    try {
      const res = await api.post<TestChatResponse>(
        '/api/test-chat',
        { messages: history.slice(-MAX_TURNS).map(toTurn) },
        { silent: true },
      )
      setEntries((e) => [
        ...e,
        { role: 'assistant', content: res.reply, tool_calls: res.tool_calls ?? [], handed_over: res.handed_over, coins: res.usage?.coins ?? null },
      ])
    } catch (err) {
      const reason = (err as Error).message || 'Бот не ответил'
      setEntries((e) => [
        ...e.map((x) => (x === turn ? { ...x, failed: true } : x)),
        { role: 'assistant', content: `${reason}. Это сообщение не попадёт в историю — можно писать дальше.`, failed: true },
      ])
    } finally {
      setBusy(false)
    }
  }

  const reset = (
    <Button size="sm" variant="ghost" disabled={!entries.length || busy} onClick={() => setEntries([])} icon={<RotateCcw className="size-3.5" aria-hidden />}>
      Начать заново
    </Button>
  )
  const spentLine =
    spent > 0 ? (
      <span className="tabular text-[12px] text-ink-3" data-testid="test-chat-spent">
        Потрачено в тесте: {formatCoins(spent)} {coinsWord(spent)}
      </span>
    ) : null

  return (
    <div className={cx('flex min-h-0 flex-col', embedded ? 'h-[min(560px,70dvh)] rounded-[10px] border border-line' : 'h-full')}>
      {embedded ? (
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2">
          {spentLine ?? <span className="text-[12px] text-ink-3">Сообщения не уходят клиентам</span>}
          {reset}
        </div>
      ) : (
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line bg-surface px-4 py-4 sm:px-6 lg:px-8">
          <div className="min-w-0">
            <h1 className="text-[18px] font-semibold text-ink">Тестовый чат</h1>
            <p className="max-w-[80ch] text-[13px] text-ink-2">
              Напишите как клиент — бот ответит на текущих настройках. WhatsApp для этого не нужен, сообщения не уходят клиентам и не попадают в «Диалоги».
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            {spentLine}
            {reset}
          </div>
        </header>
      )}

      <div ref={logRef} role="log" aria-live="polite" aria-label="Тестовая переписка" className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6 lg:px-8">
        <div className="mx-auto flex max-w-[920px] flex-col gap-3">
          {entries.length === 0 &&
            (notConfigured ? (
              <EmptyState
                title="Сначала настройте бота"
                icon={<FlaskConical className="size-5" />}
                action={
                  embedded ? undefined : (
                    <Link to="/bot" className="text-sm font-medium text-accent underline">
                      Открыть настройки бота
                    </Link>
                  )
                }
              >
                Бот отвечает по промпту, а он пока пуст. Выберите тип бизнеса и заполните данные.
              </EmptyState>
            ) : (
              <EmptyState title="Напишите боту как клиентка" icon={<FlaskConical className="size-5" />}>
                Спросите про цены, мастеров и запись. Можно проверить и голосовое или фото — увидите, что бот ответит. Под ответами видно, какие действия он выполнил.
              </EmptyState>
            ))}
          {entries.map((e, i) =>
            e.role === 'user' ? (
              <div key={i} className="max-w-[85%] self-end rounded-[12px] rounded-tr-[4px] border border-line bg-surface px-3.5 py-2 text-sm whitespace-pre-wrap text-ink">
                {e.media && (
                  <span className="flex items-center gap-1.5 text-ink-2">
                    {e.media === 'audio' ? <Mic className="size-3.5" aria-hidden /> : <ImageIcon className="size-3.5" aria-hidden />}
                    {MEDIA_LABEL[e.media]}
                  </span>
                )}
                {e.content}
              </div>
            ) : (
              <div key={i} className="flex max-w-[88%] flex-col gap-1.5 self-start">
                {e.tool_calls?.map((tc, j) => <ToolCallView key={j} call={tc} />)}
                <div
                  className={cx(
                    'rounded-[12px] rounded-tl-[4px] px-3.5 py-2 text-sm whitespace-pre-wrap',
                    e.failed ? 'border border-danger/30 bg-danger-soft text-danger' : 'bg-accent-soft text-ink',
                  )}
                >
                  {e.content}
                </div>
                {(e.handed_over || (e.coins ?? 0) > 0) && (
                  <p className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-[12px] text-ink-3">
                    {e.handed_over && (
                      <span className="inline-flex items-center gap-1 text-warn">
                        <UserRoundCheck className="size-3.5" aria-hidden />В WhatsApp диалог перешёл бы к администратору
                      </span>
                    )}
                    {(e.coins ?? 0) > 0 && (
                      <span className="tabular">
                        {formatCoins(e.coins!)} {coinsWord(e.coins!)}
                      </span>
                    )}
                  </p>
                )}
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
        className="border-t border-line bg-surface px-4 py-3 sm:px-6 lg:px-8"
        onSubmit={(e) => {
          e.preventDefault()
          void send(text)
        }}
      >
        <div className="mx-auto max-w-[920px]">
          {entries.length === 0 && !notConfigured && (
            <div className="mb-2.5 flex flex-wrap gap-1.5" aria-label="Примеры вопросов">
              {suggestions.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => void send(s)}
                  className="rounded-full border border-line px-2.5 py-1 text-left text-[13px] text-ink-2 hover:border-line-strong hover:text-ink"
                >
                  {s}
                </button>
              ))}
            </div>
          )}
          <div className="flex items-end gap-2">
            <label htmlFor={embedded ? 'test-input-embedded' : 'test-input'} className="sr-only">
              Сообщение боту
            </label>
            <textarea
              id={embedded ? 'test-input-embedded' : 'test-input'}
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
              maxLength={MAX_MESSAGE}
              aria-describedby={text.length > MAX_MESSAGE * 0.8 ? 'test-input-count' : undefined}
              className="field-control max-h-32 min-h-[38px] min-w-0 flex-1 resize-none"
            />
            <Button type="submit" variant="primary" loading={busy} disabled={!text.trim()} icon={<Send className="size-4" aria-hidden />} aria-label="Отправить">
              <span className="max-sm:sr-only">Отправить</span>
            </Button>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[12px] text-ink-3">
            {text.length > MAX_MESSAGE * 0.8 && (
              <span id="test-input-count" className={cx('tabular mr-2', text.length >= MAX_MESSAGE && 'text-warn')}>
                {text.length.toLocaleString('ru-RU')} / {MAX_MESSAGE.toLocaleString('ru-RU')}
              </span>
            )}
            <span>Проверить вложения:</span>
            <button
              type="button"
              disabled={busy || notConfigured}
              onClick={() => void send('', 'audio')}
              className="inline-flex items-center gap-1 rounded-full border border-line px-2 py-0.5 hover:border-line-strong hover:text-ink disabled:opacity-50"
            >
              <Mic className="size-3" aria-hidden />
              голосовое
            </button>
            <button
              type="button"
              disabled={busy || notConfigured}
              onClick={() => void send(text, 'image')}
              className="inline-flex items-center gap-1 rounded-full border border-line px-2 py-0.5 hover:border-line-strong hover:text-ink disabled:opacity-50"
            >
              <ImageIcon className="size-3" aria-hidden />
              фото{text.trim() ? ' с подписью' : ''}
            </button>
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
