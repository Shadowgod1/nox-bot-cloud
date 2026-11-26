import { useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { ArrowRight, Trash2, X } from 'lucide-react'
import { api } from '../../api/client'
import type { BotSettings, ClientCard, Conversation, Message } from '../../api/types'
import { useLoad } from '../../hooks/useLoad'
import { useEvent } from '../../hooks/useEvents'
import { Badge, Button, ErrorState, Spinner, cx } from '../../components/ui'
import { formatDateTime, plural, ru } from '../../i18n/ru'
import { bookingTone } from '../Bookings/Bookings'
import { StatusBadge } from './StatusBadge'

export const FORGET_CONFIRM =
  'Удалить имя и все заметки о клиенте? Бот перестанет их учитывать. Заявки останутся в разделе «Заявки».'

interface Props {
  conversationId: number
  conversation: Conversation | null
  /** Messages loaded in the chat (oldest first): for the counters. */
  messages: Message[]
  /** More history exists on the server than is loaded. */
  hasOlder: boolean
  /** xl+: a third column next to the chat; below: a sheet over the chat. */
  wide: boolean
  onClose: () => void
}

/**
 * "Что бот знает о клиенте": the client card the bot sees in every reply (name, notes, last requests) plus
 * what the admin needs around it: who answers now, message counters and how much of the chat the bot reads.
 * The owner can delete a note or everything at once (privacy policy); booking requests stay in "Заявки".
 */
export function ClientPanel({ conversationId, conversation, messages, hasOlder, wide, onClose }: Props) {
  const url = `/api/conversations/${conversationId}/client`
  const card = useLoad(() => api.get<ClientCard>(url, { silent: true }), [conversationId])
  const [busy, setBusy] = useState<string | null>(null)
  const [memory, setMemory] = useState<number | null>(null)

  useEffect(() => {
    let alive = true
    api
      .get<BotSettings>('/api/settings/bot', { silent: true })
      .then((s) => alive && setMemory(s.memory_messages))
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [])

  // a request of this client was created or decided: the card lists its last requests
  useEvent('booking.updated', ({ booking }) => {
    if (booking.conversation_id === conversationId) void card.reload()
  })

  async function removeNote(fact: string) {
    setBusy(fact)
    try {
      card.setData(await api.post<ClientCard>(`${url}/notes/remove`, { fact }))
    } catch {
      /* toast */
    } finally {
      setBusy(null)
    }
  }

  async function forget() {
    if (!window.confirm(FORGET_CONFIRM)) return
    setBusy('*')
    try {
      card.setData(await api.del<ClientCard>(url))
    } catch {
      /* toast */
    } finally {
      setBusy(null)
    }
  }

  const data = card.data
  const hasMemory = !!data && (!!data.name || data.notes.length > 0)
  const counts = {
    client: messages.filter((m) => m.author === 'client').length,
    bot: messages.filter((m) => m.author === 'bot').length,
    operator: messages.filter((m) => m.author === 'operator').length,
  }

  return (
    <aside
      aria-label="Что бот знает о клиенте"
      className={cx(
        'flex flex-col overflow-y-auto bg-surface',
        wide
          ? 'w-[320px] shrink-0 border-l border-line 2xl:w-[360px]'
          : 'absolute inset-y-0 right-0 z-20 w-full shadow-[var(--shadow-pop)] sm:w-[340px] sm:border-l sm:border-line',
      )}
    >
      <div className="flex items-start justify-between gap-2 border-b border-line px-5 py-3.5">
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold text-ink">Что бот знает о клиенте</h2>
          <p className="mt-0.5 text-[12px] text-ink-3">Бот видит это в каждом ответе. Номер телефона модели не передаётся.</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="-mr-2 grid size-8 shrink-0 place-items-center rounded-[7px] text-ink-2 hover:bg-sunken"
          aria-label="Закрыть панель"
        >
          <X className="size-4" aria-hidden />
        </button>
      </div>

      {card.loading && !data ? (
        <Spinner />
      ) : card.error && !data ? (
        <ErrorState message={card.error.message} onRetry={card.reload} />
      ) : data ? (
        <>
          <Section title="Клиент">
            <dl className="grid gap-2.5 text-sm">
              <Fact label="Имя">{data.name || <span className="text-ink-3">Клиент не называл имя</span>}</Fact>
              {conversation && (
                <Fact label="Кто отвечает">
                  <StatusBadge conversation={conversation} />
                </Fact>
              )}
            </dl>
          </Section>

          <Section title="Заметки">
            {data.notes.length ? (
              <ul className="-mx-2 flex flex-col gap-0.5 text-sm">
                {data.notes.map((n) => (
                  <li key={n} className="group flex items-start gap-2 rounded-[7px] px-2 py-1.5 hover:bg-sunken">
                    <span className="min-w-0 flex-1 break-words text-ink">{n}</span>
                    <button
                      type="button"
                      onClick={() => removeNote(n)}
                      disabled={busy !== null}
                      aria-label={`Удалить заметку «${n}»`}
                      className="shrink-0 rounded p-0.5 text-ink-3 hover:text-danger disabled:opacity-50"
                    >
                      <Trash2 className="size-4" aria-hidden />
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[13px] text-ink-3">
                Заметок пока нет. Бот добавит их сам, когда клиент расскажет о предпочтениях: мастер, удобное время, противопоказания.
              </p>
            )}
          </Section>

          <Section title="Переписка">
            <dl className="grid grid-cols-3 divide-x divide-line rounded-[9px] border border-line text-center">
              <Count value={counts.client} label="от клиента" />
              <Count value={counts.bot} label="ответил бот" />
              <Count value={counts.operator} label="оператор" />
            </dl>
            {hasOlder && <p className="mt-2 text-[12px] text-ink-3">По загруженным сообщениям; ранние можно подгрузить в чате.</p>}
            {memory != null && (
              <p className="mt-3 text-[13px] leading-relaxed text-ink-2">
                В ответе бот видит последние {memory} {plural(memory, ['сообщение', 'сообщения', 'сообщений'])} переписки; имя, заметки и
                заявки — сверх этого.
              </p>
            )}
          </Section>

          <Section
            title="Последние заявки"
            action={
              <Link to="/bookings" className="inline-flex items-center gap-1 text-[13px] font-medium text-accent hover:underline">
                Все заявки <ArrowRight className="size-3" aria-hidden />
              </Link>
            }
          >
            {data.bookings.length ? (
              <ul className="flex flex-col gap-2 text-sm">
                {data.bookings.map((b) => (
                  <li key={b.id} className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
                    <span className="min-w-0 text-ink">
                      <span className="tabular text-ink-3">#{b.id}</span> {[b.service, b.when].filter(Boolean).join(', ') || 'без деталей'}
                    </span>
                    <span title={`Создана ${formatDateTime(b.created_at)}`}>
                      <Badge tone={bookingTone[b.status]} dot>
                        {ru.bookingStatus[b.status]}
                      </Badge>
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[13px] text-ink-3">Заявок не было.</p>
            )}
          </Section>

          <div className="px-5 py-4">
            <Button
              variant="danger-ghost"
              size="sm"
              onClick={forget}
              loading={busy === '*'}
              disabled={!hasMemory || (busy !== null && busy !== '*')}
              icon={<Trash2 className="size-3.5" aria-hidden />}
            >
              Забыть клиента
            </Button>
            <p className="mt-1.5 text-[12px] text-ink-3">Удаляет имя и заметки. Заявки остаются в разделе «Заявки».</p>
          </div>
        </>
      ) : null}
    </aside>
  )
}

function Section({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="border-b border-line px-5 py-4">
      <div className="mb-2.5 flex items-baseline justify-between gap-2">
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
