import { useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { ArrowRight, X } from 'lucide-react'
import { api } from '../../api/client'
import type { Booking, BotSettings, Conversation, Message } from '../../api/types'
import { useEvent } from '../../hooks/useEvents'
import { Badge, cx } from '../../components/ui'
import { formatDateTime, plural, ru } from '../../i18n/ru'
import { bookingDetails, bookingTone } from '../Bookings/Bookings'
import { StatusBadge } from './StatusBadge'

interface Props {
  conversationId: number
  conversation: Conversation | null
  /** Messages loaded in the chat (oldest first). */
  messages: Message[]
  /** More history exists on the server than is loaded. */
  hasOlder: boolean
  /** Below xl the panel is a sheet over the chat, opened from the chat header. */
  open: boolean
  onClose: () => void
}

/**
 * "Что бот знает о клиенте": the facts the admin has about this chat: contact, status, the client's
 * booking requests and how much of the conversation the bot keeps in mind. Third column on wide screens.
 */
export function ClientPanel({ conversationId, conversation, messages, hasOlder, open, onClose }: Props) {
  const [bookings, setBookings] = useState<Booking[] | null>(null)
  const [memory, setMemory] = useState<number | null>(null)

  useEffect(() => {
    let alive = true
    // GET /api/bookings: newest first, up to 200; the client's requests are filtered here.
    api
      .get<Booking[]>('/api/bookings', { silent: true })
      .then((all) => alive && setBookings(all.filter((b) => b.conversation_id === conversationId)))
      .catch(() => alive && setBookings(null))
    api
      .get<BotSettings>('/api/settings/bot', { silent: true })
      .then((s) => alive && setMemory(s.memory_messages))
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [conversationId])

  useEvent('booking.updated', ({ booking: b }) => {
    if (b.conversation_id !== conversationId) return
    setBookings((d) => {
      const list = d ?? []
      return list.some((x) => x.id === b.id) ? list.map((x) => (x.id === b.id ? b : x)) : [b, ...list]
    })
  })

  const fromClient = messages.filter((m) => m.author === 'client')
  const lastClient = fromClient.at(-1)
  const counts = {
    client: fromClient.length,
    bot: messages.filter((m) => m.author === 'bot').length,
    operator: messages.filter((m) => m.author === 'operator').length,
  }

  return (
    <aside
      aria-label="Что бот знает о клиенте"
      className={cx(
        'w-[320px] shrink-0 flex-col overflow-y-auto border-l border-line bg-surface 2xl:w-[360px]',
        // below xl: a sheet over the chat
        'max-xl:absolute max-xl:inset-y-0 max-xl:right-0 max-xl:z-20 max-xl:w-[min(340px,100%)] max-xl:shadow-[var(--shadow-pop)]',
        open ? 'flex' : 'hidden xl:flex',
      )}
    >
      <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
        <h2 className="text-[15px] font-semibold text-ink">Что бот знает о клиенте</h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="Закрыть"
          className="-mr-2 grid size-8 place-items-center rounded-[7px] text-ink-2 hover:bg-sunken xl:hidden"
        >
          <X className="size-4" aria-hidden />
        </button>
      </div>

      <Section title="Клиент">
        <dl className="grid gap-2.5 text-sm">
          <Fact label="Имя в WhatsApp">{conversation?.name || <span className="text-ink-3">не указано</span>}</Fact>
          <Fact label="Телефон">
            <span className="tabular">{conversation?.phone ?? '—'}</span>
          </Fact>
          {conversation && (
            <Fact label="Кто отвечает">
              <StatusBadge conversation={conversation} />
            </Fact>
          )}
        </dl>
      </Section>

      <Section title="Переписка">
        <dl className="grid gap-2.5 text-sm">
          {!hasOlder && messages[0] && <Fact label="Первое сообщение">{formatDateTime(messages[0].created_at)}</Fact>}
          <Fact label="Последнее от клиента">{lastClient ? formatDateTime(lastClient.created_at) : <span className="text-ink-3">—</span>}</Fact>
        </dl>
        <dl className="mt-3 grid grid-cols-3 divide-x divide-line rounded-[9px] border border-line text-center">
          <Count value={counts.client} label="от клиента" />
          <Count value={counts.bot} label="ответил бот" />
          <Count value={counts.operator} label="оператор" />
        </dl>
        {hasOlder && <p className="mt-2 text-[12px] text-ink-3">По загруженным сообщениям; ранние можно подгрузить в чате.</p>}
        {memory != null && (
          <p className="mt-3 text-[13px] leading-relaxed text-ink-2">
            В ответе бот учитывает последние {memory} {plural(memory, ['сообщение', 'сообщения', 'сообщений'])} переписки и данные из
            настроек бота.
          </p>
        )}
      </Section>

      <Section
        title="Заявки клиента"
        action={
          <Link to="/bookings" className="inline-flex items-center gap-1 text-[13px] font-medium text-accent hover:underline">
            Все заявки <ArrowRight className="size-3" aria-hidden />
          </Link>
        }
      >
        {bookings == null ? (
          <p className="text-[13px] text-ink-3">Список заявок недоступен.</p>
        ) : bookings.length === 0 ? (
          <p className="text-[13px] text-ink-2">Заявок от клиента пока нет.</p>
        ) : (
          <ul className="grid gap-2">
            {bookings.map((b) => (
              <li key={b.id} className="rounded-[9px] border border-line px-3 py-2.5">
                <div className="flex items-start justify-between gap-2">
                  <span className="text-[13px] font-medium text-ink">{bookingDetails(b) || 'Без деталей'}</span>
                  <Badge tone={bookingTone[b.status]} dot>
                    {ru.bookingStatus[b.status]}
                  </Badge>
                </div>
                {b.payload?.comment && <p className="mt-1 text-[13px] text-ink-2">«{b.payload.comment}»</p>}
                <p className="tabular mt-1 text-[12px] text-ink-3">
                  #{b.id} · {formatDateTime(b.created_at)}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </aside>
  )
}

function Section({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="border-b border-line px-5 py-4 last:border-b-0">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <h3 className="text-[12px] font-semibold tracking-[0.04em] text-ink-3 uppercase">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  )
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="shrink-0 text-[13px] text-ink-2">{label}</dt>
      <dd className="min-w-0 text-right break-words text-ink">{children}</dd>
    </div>
  )
}

function Count({ value, label }: { value: number; label: string }) {
  return (
    <div className="flex flex-col-reverse px-2 py-2.5">
      <dt className="mt-0.5 text-[12px] leading-tight text-ink-2">{label}</dt>
      <dd className="tabular text-[17px] font-semibold leading-none text-ink">{value}</dd>
    </div>
  )
}
