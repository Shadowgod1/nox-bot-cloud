import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { Search } from 'lucide-react'
import { api, formatDate, STATUS_LABELS, type ApplicationListItem, type Status } from '../../api'
import { Shell } from '../../components/Shell'
import { Alert, Card, cx, Input, StatusBadge } from '../../components/ui'
import { useStaff } from './useStaff'

const FILTERS: (Status | 'all')[] = [
  'all', 'verified', 'awaiting_invoice', 'payment_pending', 'awaiting_payment', 'new', 'approved', 'provisioning', 'active',
  'failed', 'rejected', 'suspended',
]

export function Applications() {
  const { staff, logout } = useStaff()
  const [filter, setFilter] = useState<Status | 'all'>('all')
  const [q, setQ] = useState('')
  const [rows, setRows] = useState<ApplicationListItem[] | null>(null)
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!staff) return
    const params = new URLSearchParams()
    if (filter !== 'all') params.set('status', filter)
    if (q.trim()) params.set('q', q.trim())
    const t = setTimeout(() => {
      api
        .get<ApplicationListItem[]>(`/api/staff/applications?${params}`)
        .then((r) => {
          setRows(r)
          setError(null)
        })
        .catch((err) => setError(err instanceof Error ? err.message : 'Ошибка загрузки'))
      api.get<Record<string, number>>('/api/staff/counts').then(setCounts).catch(() => undefined)
    }, 200)
    return () => clearTimeout(t)
  }, [staff, filter, q])

  const total = Object.values(counts).reduce((a, b) => a + b, 0)

  return (
    <Shell subtitle="Заявки" home="/staff" user={staff?.email} onLogout={logout} wide>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-[22px] font-extrabold tracking-tight text-ink">Заявки</h1>
        <Link to="/staff/local" className="ml-auto text-[13.5px] font-semibold text-accent hover:text-accent-hover">
          Локальная установка
        </Link>
        <Link to="/staff/audit" className="text-[13.5px] font-semibold text-accent hover:text-accent-hover">
          Журнал действий
        </Link>
      </div>
      <div className="mt-4 flex flex-wrap gap-1.5" role="tablist" aria-label="Фильтр по статусу">
        {FILTERS.map((f) => {
          const n = f === 'all' ? total : counts[f] ?? 0
          // empty statuses only add noise: show a chip when it has applications or is the current filter
          if (f !== 'all' && n === 0 && filter !== f) return null
          return (
            <button
              key={f}
              role="tab"
              aria-selected={filter === f}
              onClick={() => setFilter(f)}
              className={cx(
                'h-8 rounded-nx border px-3 text-[13px] font-semibold',
                filter === f ? 'border-accent bg-accent text-on-accent' : 'border-line bg-surface text-ink-2 hover:bg-sunken',
              )}
            >
              {f === 'all' ? 'Все' : STATUS_LABELS[f]}
              <span className={cx('ml-1.5 tabular-nums', filter === f ? 'text-on-accent/75' : 'text-ink-3')}>{n}</span>
            </button>
          )
        })}
      </div>
      <div className="relative mt-3 max-w-[360px]">
        <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-ink-3" aria-hidden />
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Компания, e-mail, адрес" className="pl-9" aria-label="Поиск" />
      </div>
      {error && (
        <div className="mt-4">
          <Alert>{error}</Alert>
        </div>
      )}
      <Card className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[760px] text-left text-[14px]">
          <thead>
            <tr className="border-b border-line bg-sunken/60 text-[12.5px] font-semibold text-ink-3">
              <th className="px-4 py-2.5">Компания</th>
              <th className="px-4 py-2.5">Контакт</th>
              <th className="px-4 py-2.5">Адрес</th>
              <th className="px-4 py-2.5">Статус</th>
              <th className="px-4 py-2.5">Подана</th>
            </tr>
          </thead>
          <tbody>
            {rows?.map((r) => (
              <tr key={r.id} className="border-b border-line transition-colors last:border-0 hover:bg-sunken">
                <td className="px-4 py-2.5">
                  <Link to={`/staff/applications/${r.id}`} className="font-semibold text-ink hover:text-accent">
                    {r.company}
                  </Link>
                </td>
                <td className="px-4 py-2.5 text-ink-2">
                  {r.contact_name}
                  <div className="text-[12.5px] text-ink-3">{r.email}</div>
                </td>
                <td className="px-4 py-2.5 font-mono text-[13px] text-ink-2">{r.slug}</td>
                <td className="px-4 py-2.5">
                  <StatusBadge status={r.status} />
                </td>
                <td className="px-4 py-2.5 text-ink-3 tabular-nums">{formatDate(r.created_at)}</td>
              </tr>
            ))}
            {!rows && !error && (
              <tr>
                <td colSpan={5} className="px-4 py-10 text-center text-ink-3">
                  Загружаем заявки…
                </td>
              </tr>
            )}
            {rows && rows.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-10 text-center text-ink-3">
                  Заявок с этим статусом нет
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>
    </Shell>
  )
}
