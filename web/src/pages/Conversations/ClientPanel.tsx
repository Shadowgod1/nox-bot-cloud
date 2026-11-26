import { useState } from 'react'
import { Trash2, X } from 'lucide-react'
import { api } from '../../api/client'
import type { ClientCard } from '../../api/types'
import { useLoad } from '../../hooks/useLoad'
import { Badge, Button, ErrorState, Spinner } from '../../components/ui'
import { formatDateTime, ru } from '../../i18n/ru'

const tone = { pending: 'warn', confirmed: 'ok', rejected: 'danger', expired: 'neutral' } as const

export const FORGET_CONFIRM =
  'Удалить имя и все заметки о клиенте? Бот перестанет их учитывать. Заявки останутся в разделе «Заявки».'

/**
 * "Что бот знает о клиенте": the client card the bot sees in every reply. The owner can delete a note or
 * everything at once (privacy policy); booking requests are business records and stay in "Заявки".
 */
export function ClientPanel({ conversationId, onClose }: { conversationId: number; onClose: () => void }) {
  const url = `/api/conversations/${conversationId}/client`
  const card = useLoad(() => api.get<ClientCard>(url, { silent: true }), [conversationId])
  const [busy, setBusy] = useState<string | null>(null)

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

  return (
    <aside
      aria-label="Что бот знает о клиенте"
      className="absolute inset-0 z-10 flex flex-col overflow-y-auto bg-surface lg:static lg:inset-auto lg:w-[320px] lg:shrink-0 lg:border-l lg:border-line"
    >
      <div className="flex items-start justify-between gap-2 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-ink">Что бот знает о клиенте</h3>
          <p className="mt-0.5 text-[12px] text-ink-3">Бот видит это в каждом ответе. Номер телефона модели не передаётся.</p>
        </div>
        <button type="button" onClick={onClose} className="-mr-1 rounded p-1 text-ink-2 hover:bg-sunken" aria-label="Закрыть панель">
          <X className="size-4" aria-hidden />
        </button>
      </div>

      {card.loading && !data ? (
        <Spinner />
      ) : card.error && !data ? (
        <ErrorState message={card.error.message} onRetry={card.reload} />
      ) : data ? (
        <div className="flex flex-col gap-5 px-4 py-4 text-sm">
          <section>
            <h4 className="text-[12px] font-medium tracking-wide text-ink-3 uppercase">Имя</h4>
            <p className="mt-1 text-ink">{data.name || <span className="text-ink-3">Клиент не называл имя</span>}</p>
          </section>

          <section>
            <h4 className="text-[12px] font-medium tracking-wide text-ink-3 uppercase">Заметки</h4>
            {data.notes.length ? (
              <ul className="-mx-2 mt-1 flex flex-col gap-0.5">
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
              <p className="mt-1 text-[13px] text-ink-3">
                Заметок пока нет. Бот добавит их сам, когда клиент расскажет о предпочтениях: мастер, удобное время, противопоказания.
              </p>
            )}
          </section>

          <section>
            <h4 className="text-[12px] font-medium tracking-wide text-ink-3 uppercase">Последние заявки</h4>
            {data.bookings.length ? (
              <ul className="mt-1.5 flex flex-col gap-2">
                {data.bookings.map((b) => (
                  <li key={b.id} className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
                    <span className="min-w-0 text-ink">
                      <span className="tabular text-ink-3">#{b.id}</span> {[b.service, b.when].filter(Boolean).join(', ') || 'без деталей'}
                    </span>
                    <span title={`Создана ${formatDateTime(b.created_at)}`}>
                      <Badge tone={tone[b.status]} dot>
                        {ru.bookingStatus[b.status]}
                      </Badge>
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1 text-[13px] text-ink-3">Заявок не было.</p>
            )}
          </section>

          <div className="border-t border-line pt-4">
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
        </div>
      ) : null}
    </aside>
  )
}
