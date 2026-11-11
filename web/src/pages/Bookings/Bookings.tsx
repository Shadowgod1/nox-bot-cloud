import { useState } from 'react'
import { Link } from 'react-router'
import { CalendarCheck, Check, Clock, MessageSquare, X } from 'lucide-react'
import { api, qs } from '../../api/client'
import type { Booking, BookingStatus, Page } from '../../api/types'
import { useLoad } from '../../hooks/useLoad'
import { useEvent } from '../../hooks/useEvents'
import { PageBody } from '../../components/Layout'
import { Badge, Button, EmptyState, ErrorState, Field, PageHeader, Segmented, Spinner, Textarea, cx } from '../../components/ui'
import { toast } from '../../components/toast'
import { formatDateTime, plural, ru } from '../../i18n/ru'

type Filter = 'all' | BookingStatus

const tone = { pending: 'warn', confirmed: 'ok', rejected: 'danger', expired: 'neutral' } as const

function normalize(r: Page<Booking> | Booking[]): Booking[] {
  return Array.isArray(r) ? r : r.items
}

export function Bookings() {
  const [filter, setFilter] = useState<Filter>('pending')
  const list = useLoad(
    () => api.get<Page<Booking> | Booking[]>(`/api/bookings${qs({ status: filter === 'all' ? null : filter })}`, { silent: true }).then(normalize),
    [filter],
  )

  useEvent('booking.updated', (b) => {
    const known = list.data?.some((x) => x.id === b.id)
    if (known) list.setData((d) => d?.map((x) => (x.id === b.id ? { ...x, ...b } : x)))
    else void list.reload()
  })

  const replace = (b: Booking) => list.setData((d) => d?.map((x) => (x.id === b.id ? b : x)))

  return (
    <PageBody>
      <PageHeader
        title="Заявки на бронь"
        description="Бот собирает заявки в переписке. Подтвердите или отклоните — клиент получит ответ по шаблону из настроек бота."
      />
      <div className="mb-4">
        <Segmented<Filter>
          label="Фильтр заявок"
          value={filter}
          onChange={setFilter}
          options={[
            { value: 'pending', label: 'Ожидают' },
            { value: 'confirmed', label: 'Подтверждённые' },
            { value: 'rejected', label: 'Отклонённые' },
            { value: 'expired', label: 'Истёкшие' },
            { value: 'all', label: 'Все' },
          ]}
        />
      </div>
      <div className="rounded-[var(--radius-panel)] border border-line bg-surface">
        {list.loading && !list.data ? (
          <Spinner />
        ) : list.error && !list.data ? (
          <ErrorState message={list.error.message} onRetry={list.reload} />
        ) : !list.data?.length ? (
          <EmptyState title={filter === 'pending' ? 'Новых заявок нет' : 'Заявок нет'} icon={<CalendarCheck className="size-5" />}>
            {filter === 'pending' ? 'Когда клиент попросит забронировать место, заявка появится здесь.' : 'Попробуйте другой фильтр.'}
          </EmptyState>
        ) : (
          <ul className="divide-y divide-line">
            {list.data.map((b) => (
              <BookingRow key={b.id} booking={b} onChange={replace} />
            ))}
          </ul>
        )}
      </div>
    </PageBody>
  )
}

function details(b: Booking) {
  const p = b.payload ?? {}
  const parts: string[] = []
  if (p.date) {
    const d = new Date(`${p.date}T00:00:00`)
    parts.push(Number.isNaN(d.getTime()) ? p.date : d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', weekday: 'short' }))
  }
  if (p.time) parts.push(p.time)
  if (p.duration_hours) parts.push(`${p.duration_hours} ч`)
  if (p.seats) parts.push(`${p.seats} ${plural(p.seats, ['место', 'места', 'мест'])}`)
  if (p.zone) parts.push(p.zone)
  return parts.join(' · ')
}

function BookingRow({ booking: b, onChange }: { booking: Booking; onChange: (b: Booking) => void }) {
  const [mode, setMode] = useState<null | 'confirm' | 'reject'>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const who = b.client_name || b.payload?.name || b.phone || `Диалог #${b.conversation_id}`

  async function decide() {
    if (!mode) return
    setBusy(true)
    try {
      const res = await api.post<Booking | undefined>(`/api/bookings/${b.id}/${mode}`, { note: note.trim() || undefined })
      const status: BookingStatus = mode === 'confirm' ? 'confirmed' : 'rejected'
      onChange(res && typeof res === 'object' && 'id' in res ? res : { ...b, status, admin_note: note.trim() || null, decided_at: new Date().toISOString() })
      toast.success(mode === 'confirm' ? `Заявка #${b.id} подтверждена` : `Заявка #${b.id} отклонена`)
      setMode(null)
      setNote('')
    } catch {
      /* toast */
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="tabular text-[13px] text-ink-3">#{b.id}</span>
            <span className="text-sm font-semibold text-ink">{details(b) || 'Без деталей'}</span>
            <Badge tone={tone[b.status]} dot>{ru.bookingStatus[b.status]}</Badge>
            {b.timeout_notified && b.status === 'pending' && (
              <Badge tone="danger">
                <Clock className="size-3" aria-hidden /> Долго без ответа
              </Badge>
            )}
          </div>
          <p className="mt-1 text-[13px] text-ink-2">
            {who}
            {b.phone && who !== b.phone && <span className="tabular"> · {b.phone}</span>}
            <span className="text-ink-3"> · создана {formatDateTime(b.created_at)}</span>
          </p>
          {b.payload?.comment && <p className="mt-1.5 text-sm text-ink">«{b.payload.comment}»</p>}
          {b.status !== 'pending' && (b.admin_note || b.decided_by) && (
            <p className="mt-1.5 text-[13px] text-ink-2">
              {b.admin_note && <>Комментарий: {b.admin_note}. </>}
              {b.decided_by && <span className="text-ink-3">Решение: {b.decided_by}{b.decided_at ? `, ${formatDateTime(b.decided_at)}` : ''}</span>}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Link
            to={`/conversations/${b.conversation_id}`}
            className="inline-flex h-8 items-center gap-1.5 rounded-[7px] px-2.5 text-[13px] font-medium text-ink-2 hover:bg-sunken hover:text-ink"
          >
            <MessageSquare className="size-3.5" aria-hidden /> Диалог
          </Link>
          {b.status === 'pending' && !mode && (
            <>
              <Button size="sm" variant="secondary" onClick={() => setMode('reject')} icon={<X className="size-3.5" aria-hidden />}>
                Отклонить
              </Button>
              <Button size="sm" variant="primary" onClick={() => setMode('confirm')} icon={<Check className="size-3.5" aria-hidden />}>
                Подтвердить
              </Button>
            </>
          )}
        </div>
      </div>
      {mode && (
        <form
          className={cx('mt-3 flex flex-col gap-3 rounded-[9px] border p-3.5', mode === 'confirm' ? 'border-ok/30 bg-ok-soft/60' : 'border-danger/25 bg-danger-soft/60')}
          onSubmit={(e) => {
            e.preventDefault()
            void decide()
          }}
        >
          <Field
            label={mode === 'confirm' ? 'Комментарий для клиента (необязательно)' : 'Причина отказа'}
            hint={mode === 'confirm' ? 'Подставится в шаблон подтверждения, например «места 14–15».' : 'Клиент увидит её в сообщении об отказе.'}
          >
            <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} autoFocus className="min-h-14 bg-surface" />
          </Field>
          <div className="flex gap-2">
            <Button type="submit" size="sm" variant={mode === 'confirm' ? 'primary' : 'danger'} loading={busy}>
              {mode === 'confirm' ? 'Подтвердить заявку' : 'Отклонить заявку'}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setMode(null)}>
              {ru.common.cancel}
            </Button>
          </div>
        </form>
      )}
    </li>
  )
}
