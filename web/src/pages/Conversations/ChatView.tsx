import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { ArrowLeft, Bot, Hand, Send, UserRound } from 'lucide-react'
import { api } from '../../api/client'
import type { Conversation, Message, Ok, Page } from '../../api/types'
import { useEvent } from '../../hooks/useEvents'
import { XL_QUERY, useMediaQuery } from '../../hooks/useMediaQuery'
import { Button, EmptyState, ErrorState, Spinner, cx } from '../../components/ui'
import { formatDateTime, formatTime, ru } from '../../i18n/ru'
import { ClientPanel } from './ClientPanel'
import { StatusBadge } from './StatusBadge'
import { conversationTitle, normalizeMessageEvent, realName } from './normalize'

interface Props {
  conversationId: number
  conversation: Conversation | null
  onConversation: (c: Partial<Conversation> & { id: number }) => void
}

/** Pages come newest first; the chat shows oldest first, so merge by id and sort ascending. */
export function mergeMessages(a: Message[], b: Message[]): Message[] {
  const map = new Map<number, Message>()
  for (const m of a) map.set(m.id, m)
  for (const m of b) map.set(m.id, m)
  return [...map.values()].sort((x, y) => x.id - y.id)
}

const messagesUrl = (id: number, cursor?: string | null) =>
  `/api/conversations/${id}/messages${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`

function dayKey(iso: string) {
  return new Date(iso).toDateString()
}

/** Idempotency key of one operator message (core: `^[A-Za-z0-9_-]{8,64}$`). getRandomValues, unlike
 * randomUUID, also works on plain http in the LAN. */
export function newRequestId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

const dayFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', weekday: 'short' })

export function ChatView({ conversationId, conversation, onConversation }: Props) {
  const [messages, setMessages] = useState<Message[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const olderLoaded = useRef(false)
  const [loading, setLoading] = useState(true)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  // The request id of a send that failed: retrying the same text in the same chat reuses it, so core sends
  // the message at most once even if the failed attempt actually reached WhatsApp.
  const pendingSend = useRef<{ conversationId: number; text: string; requestId: string } | null>(null)
  const [acting, setActing] = useState(false)
  // The client panel: a third column on xl+ (open by default), a sheet over the chat below (opened on demand).
  const wide = useMediaQuery(XL_QUERY)
  const [showClient, setShowClient] = useState(wide)
  useEffect(() => setShowClient(wide), [wide])
  const logRef = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const page = await api.get<Page<Message>>(messagesUrl(conversationId), { silent: true })
      setMessages((prev) => mergeMessages(prev, page.items))
      // Keep the cursor of already loaded older pages; a refresh only brings in the newest messages.
      if (!olderLoaded.current) setCursor(page.next_cursor)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId])

  async function loadOlder() {
    if (cursor == null) return
    setLoadingOlder(true)
    stickToBottom.current = false
    try {
      const page = await api.get<Page<Message>>(messagesUrl(conversationId, cursor))
      olderLoaded.current = true
      setMessages((prev) => mergeMessages(page.items, prev))
      setCursor(page.next_cursor)
    } catch {
      /* toast */
    } finally {
      setLoadingOlder(false)
    }
  }

  useEvent('message.new', (data) => {
    const m = normalizeMessageEvent(data)
    if (m.conversation_id !== conversationId) return
    const el = logRef.current
    stickToBottom.current = !el || el.scrollHeight - el.scrollTop - el.clientHeight < 120
    setMessages((prev) => mergeMessages(prev, [m]))
  })

  useLayoutEffect(() => {
    const el = logRef.current
    if (el && stickToBottom.current && typeof el.scrollTo === 'function') el.scrollTo({ top: el.scrollHeight })
  }, [messages])

  async function send() {
    const t = text.trim()
    if (!t) return
    const prev = pendingSend.current
    const requestId = prev && prev.conversationId === conversationId && prev.text === t ? prev.requestId : newRequestId()
    pendingSend.current = { conversationId, text: t, requestId }
    setSending(true)
    stickToBottom.current = true
    try {
      // Core answers {ok: true}; the message itself arrives via SSE `message.new`.
      await api.post<Ok>(`/api/conversations/${conversationId}/send`, { text: t, request_id: requestId })
      pendingSend.current = null // the next message gets a new id
      setText('')
      // Fallback when the event stream is down: pull the newest page.
      setTimeout(() => {
        void api
          .get<Page<Message>>(messagesUrl(conversationId), { silent: true })
          .then((page) => setMessages((prev) => mergeMessages(prev, page.items)))
          .catch(() => undefined)
      }, 1500)
    } catch {
      /* toast; keep the draft */
    } finally {
      setSending(false)
    }
  }

  async function act(kind: 'takeover' | 'release') {
    setActing(true)
    try {
      onConversation(await api.post<Conversation>(`/api/conversations/${conversationId}/${kind}`))
    } catch {
      /* toast */
    } finally {
      setActing(false)
    }
  }

  const botActive = !conversation || conversation.status === 'active'

  return (
    <div className="relative flex min-h-0 min-w-0 flex-1">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="flex flex-wrap items-center gap-3 border-b border-line bg-surface px-4 py-3 sm:px-5">
          <Link to="/conversations" className="-ml-1 rounded p-1 text-ink-2 hover:bg-sunken md:hidden" aria-label="К списку диалогов">
            <ArrowLeft className="size-4" aria-hidden />
          </Link>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-[15px] font-semibold text-ink">
              {conversation ? conversationTitle(conversation) : `Диалог #${conversationId}`}
            </h2>
            <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[13px] text-ink-2">
              {realName(conversation?.name) && <span className="tabular max-w-full truncate">{conversation?.phone}</span>}
              {conversation && (
                <span className="max-sm:hidden">
                  <StatusBadge conversation={conversation} />
                </span>
              )}
            </div>
          </div>
          <Button
            variant={showClient ? 'primary' : 'ghost'}
            onClick={() => setShowClient((v) => !v)}
            aria-pressed={showClient}
            aria-label="Что бот знает о клиенте"
            title="Что бот знает о клиенте"
            className="max-sm:px-2"
            icon={<UserRound className="size-4" aria-hidden />}
          >
            <span className="max-sm:hidden">Клиент</span>
          </Button>
          {botActive ? (
            <Button onClick={() => act('takeover')} loading={acting} icon={<Hand className="size-4" aria-hidden />}>
              Перехватить
            </Button>
          ) : (
            <Button onClick={() => act('release')} loading={acting} icon={<Bot className="size-4" aria-hidden />}>
              Вернуть бота
            </Button>
          )}
          {/* phones: the status gets its own row so "Ведёт оператор до 21:30" is never cut by the button */}
          {conversation && (
            <div className="basis-full sm:hidden">
              <StatusBadge conversation={conversation} />
            </div>
          )}
        </header>

        <div ref={logRef} role="log" aria-live="polite" aria-label="Сообщения" className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-5">
          {loading && messages.length === 0 ? (
            <Spinner />
          ) : error && messages.length === 0 ? (
            <ErrorState message={error} onRetry={load} />
          ) : messages.length === 0 ? (
            <EmptyState title="Сообщений нет">В этом диалоге ещё нет сообщений.</EmptyState>
          ) : (
            <div className="mx-auto flex max-w-[920px] flex-col gap-1.5">
              {cursor != null && (
                <div className="mb-2 flex justify-center">
                  <Button size="sm" variant="ghost" loading={loadingOlder} onClick={loadOlder}>
                    Показать более ранние
                  </Button>
                </div>
              )}
              {messages.map((m, i) => {
                const prev = messages[i - 1]
                const newDay = !prev || dayKey(prev.created_at) !== dayKey(m.created_at)
                const sameAuthor = prev && !newDay && prev.author === m.author
                return (
                  <div key={m.id} className="contents">
                    {newDay && (
                      <div className="my-3 text-center text-[12px] text-ink-3" role="separator">
                        {dayFmt.format(new Date(m.created_at))}
                      </div>
                    )}
                    <MessageBubble m={m} showAuthor={!sameAuthor} />
                  </div>
                )
              })}
            </div>
          )}
        </div>

        <form
          className="border-t border-line bg-surface px-4 py-3 sm:px-5"
          onSubmit={(e) => {
            e.preventDefault()
            void send()
          }}
        >
          <div className="mx-auto flex max-w-[920px] items-end gap-2">
            <label htmlFor="composer" className="sr-only">
              Ответ клиенту
            </label>
            <textarea
              id="composer"
              rows={2}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault()
                  void send()
                }
              }}
              placeholder={botActive ? 'Ответ от оператора. Бот встанет на паузу.' : 'Ответ клиенту'}
              className="field-control max-h-40 min-h-[42px] resize-none leading-snug"
            />
            <Button type="submit" variant="primary" loading={sending} disabled={!text.trim()} icon={<Send className="size-4" aria-hidden />}>
              Отправить
            </Button>
          </div>
          <p className="mx-auto mt-1.5 max-w-[920px] text-[12px] text-ink-3 max-sm:hidden">Ctrl + Enter — отправить</p>
        </form>
      </div>
      {showClient && (
        <ClientPanel
          conversationId={conversationId}
          conversation={conversation}
          messages={messages}
          hasOlder={cursor != null}
          wide={wide}
          onClose={() => setShowClient(false)}
        />
      )}
    </div>
  )
}

function MessageBubble({ m, showAuthor }: { m: Message; showAuthor: boolean }) {
  const mine = m.direction === 'out'
  if (m.author === 'system') {
    return <p className="my-1 text-center text-[12px] text-ink-3">{m.text}</p>
  }
  return (
    <div className={cx('flex flex-col', mine ? 'items-end' : 'items-start', showAuthor && 'mt-2')}>
      {showAuthor && mine && (
        <span className="mb-0.5 px-1 text-[12px] font-medium text-ink-3">{ru.author[m.author]}</span>
      )}
      <div
        title={formatDateTime(m.created_at)}
        className={cx(
          'max-w-[min(560px,85%)] rounded-[12px] px-3.5 py-2 text-sm leading-relaxed break-words whitespace-pre-wrap',
          !mine && 'rounded-tl-[4px] border border-line bg-surface text-ink',
          mine && m.author === 'bot' && 'rounded-tr-[4px] bg-accent-soft text-ink',
          mine && m.author === 'operator' && 'rounded-tr-[4px] bg-operator text-ink',
        )}
      >
        {m.text || <span className="text-ink-3 italic">вложение</span>}
        <span className="tabular ml-2 inline-block translate-y-0.5 text-[11px] text-ink-3">{formatTime(m.created_at)}</span>
      </div>
    </div>
  )
}
