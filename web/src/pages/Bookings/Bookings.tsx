import { useState } from 'react'
import { Link } from 'react-router'
import { CalendarCheck, Check, Clock, MessageSquare, X } from 'lucide-react'
import { ApiError, api, qs } from '../../api/client'
import type { Booking, BookingStatus } from '../../api/types'
import { useLoad } from '../../hooks/useLoad'
import { useEvent } from '../../hooks/useEvents'
import { PageBody } from '../../components/Layout'
import { Badge, Button, EmptyState, ErrorState, Field, PageHeader, Segmented, Spinner, Textarea, cx } from '../../components/ui'
import { toast } from '../../components/toast'
import { formatDateTime, plural, ru } from '../../i18n/ru'

type Filter = 'all' | BookingStatus

/** Columns of the request table on xl+ (header and rows): request, client, status, actions. */
const ROW_GRID = 'xl:grid xl:grid-cols-[minmax(0,2fr)_minmax(0,1.2fr)_minmax(0,180px)_340px] xl:gap-x-6'

export const bookingTone = { pending: 'warn', confirmed: 'ok', rejected: 'danger', expired: 'neutral' } as const

export function Bookings() {
  const [filter, setFilter] = useState<Filter>('pending')
  // GET /api/bookings returns a plain array, newest first.
  const list = useLoad(
    () => api.get<Booking[]>(`/api/bookings${qs({ status: filter === 'all' ? null : filter })}`, { silent: true }),
    [filter],
  )

  useEvent('booking.updated', ({ booking: b }) => {
    list.setData((d) => {
      if (!d) return d
      if (d.some((x) => x.id === b.id)) return d.map((x) => (x.id === b.id ? b : x))
      if (filter !== 'all' && b.status !== filter) return d
      return [b, ...d].sort((x, y) => y.id - x.id)
    })
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
          <>
            <div aria-hidden className={cx('hidden border-b border-line px-5 py-2.5 text-[12px] font-medium text-ink-3', ROW_GRID)}>
              <span>Заявка</span>
              <span>Клиент</span>
              <span>Статус</span>
              <span className="text-right">Действия</span>
            </div>
            <ul className="divide-y divide-line">
              {list.data.map((b) => (
                <BookingRow key={b.id} booking={b} onChange={replace} onStale={() => void list.reload()} />
              ))}
            </ul>
          </>
        )}
      </div>
    </PageBody>
  )
}

/** "чт, 12 ноября · 19:00 · 3 ч · 5 мест · VIP": the request fields core knows how to describe. */
export function bookingDetails(b: Booking) {
  const p = b.payload ?? {}
  const parts: string[] = []
  if (p.date) {
    const d = new Date(`${p.date}T00:00:00`)
    parts.push(Number.isNaN(d.getTime()) ? String(p.date) : d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', weekday: 'short' }))
  }
  if (p.time) parts.push(String(p.time))
  if (p.duration_hours !== undefined && p.duration_hours !== '') parts.push(`${p.duration_hours} ч`)
  if (p.seats !== undefined && p.seats !== '') {
    const n = Number(p.seats)
    parts.push(Number.isFinite(n) ? `${n} ${plural(n, ['место', 'места', 'мест'])}` : `мест: ${p.seats}`)
  }
  if (p.zone) parts.push(String(p.zone))
  return parts.join(' · ')
}

function BookingRow({ booking: b, onChange, onStale }: { booking: Booking; onChange: (b: Booking) => void; onStale: () => void }) {
  const [mode, setMode] = useState<null | 'confirm' | 'reject'>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const who = b.name || b.payload?.name || b.phone || `Диалог #${b.conversation_id}`

  async function decide() {
    if (!mode) return
    setBusy(true)
    try {
      // Body is optional in core: {note?: string}. Returns the updated BookingRequest.
      const res = await api.post<Booking>(`/api/bookings/${b.id}/${mode}`, { note: note.trim() || null })
      onChange(res)
      toast.success(mode === 'confirm' ? `Заявка #${b.id} подтверждена` : `Заявка #${b.id} отклонена`)
      setMode(null)
      setNote('')
    } catch (e) {
      // 409 already_decided: someone answered first (e.g. "+N" in WhatsApp) — show the current state.
      if (e instanceof ApiError && e.status === 409) {
        setMode(null)
        onStale()
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="px-4 py-4 sm:px-5">
      {/* phones..lg: stacked card; xl+: a table row (columns shared with the header in Bookings) */}
      <div className={cx('flex flex-col gap-2', ROW_GRID, 'xl:items-start xl:gap-y-1.5')}>
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1 xl:col-start-1 xl:row-start-1">
          <span className="tabular text-[13px] text-ink-3">#{b.id}</span>
          <span className="text-sm font-semibold text-ink">{bookingDetails(b) || 'Без деталей'}</span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 xl:col-start-3 xl:row-span-2 xl:row-start-1">
          <Badge tone={bookingTone[b.status]} dot>{ru.bookingStatus[b.status]}</Badge>
          {b.timeout_notified && b.status === 'pending' && (
            <Badge tone="danger">
              <Clock className="size-3" aria-hidden /> Долго без ответа
            </Badge>
          )}
        </div>
        <p className="min-w-0 text-[13px] text-ink-2 xl:col-start-2 xl:row-span-2 xl:row-start-1">
          <span className="xl:block xl:truncate xl:text-sm xl:font-medium xl:text-ink">{who}</span>
          {b.phone && who !== b.phone && (
            <span className="tabular">
              <span className="xl:hidden"> · </span>
              {b.phone}
            </span>
          )}
          <span className="text-ink-3 xl:block">
            <span className="xl:hidden"> · </span>создана {formatDateTime(b.created_at)}
          </span>
        </p>
        {(b.payload?.comment || (b.status !== 'pending' && (b.admin_note || b.decided_by))) && (
          <div className="min-w-0 xl:col-start-1 xl:row-start-2">
            {b.payload?.comment && <p className="text-sm text-ink">«{b.payload.comment}»</p>}
            {b.status !== 'pending' && (b.admin_note || b.decided_by) && (
              <p className="mt-1 text-[13px] text-ink-2">
                {b.admin_note && <>Комментарий: {b.admin_note}. </>}
                {b.decided_by && <span className="text-ink-3">Решение: {b.decided_by}{b.decided_at ? `, ${formatDateTime(b.decided_at)}` : ''}</span>}
              </p>
            )}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2 pt-1 xl:col-start-4 xl:row-span-2 xl:row-start-1 xl:justify-end xl:pt-0">
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
          className={cx('mt-3 flex max-w-[720px] flex-col gap-3 rounded-[9px] border p-3.5', mode === 'confirm' ? 'border-ok/30 bg-ok-soft/60' : 'border-danger/25 bg-danger-soft/60')}
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
