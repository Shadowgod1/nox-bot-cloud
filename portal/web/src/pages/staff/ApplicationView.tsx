import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router'
import { ArrowLeft } from 'lucide-react'
import { api, formatDate, type ApplicationDetail, type AuditOut, type StaffConfig } from '../../api'
import { Shell } from '../../components/Shell'
import { Alert, Button, Card, Field, Input, StatusBadge } from '../../components/ui'
import { ApproveForm } from './ApproveForm'
import { useStaff } from './useStaff'

const JOB_KIND: Record<string, string> = { create: 'Создание', suspend: 'Приостановка', resume: 'Возобновление', remove: 'Удаление' }
const JOB_STATUS: Record<string, string> = { pending: 'в очереди', claimed: 'выполняется', done: 'готово', failed: 'ошибка' }

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card className="p-5">
      <h2 className="mb-4 text-[15px] font-bold text-ink">{title}</h2>
      {children}
    </Card>
  )
}

export function AuditList({ rows }: { rows: AuditOut[] }) {
  if (!rows.length) return <p className="text-[13.5px] text-ink-3">Записей нет</p>
  return (
    <ul className="flex flex-col divide-y divide-line text-[13.5px]">
      {rows.map((a) => (
        <li key={a.id} className="flex flex-wrap gap-x-3 py-2">
          <span className="text-ink-3 tabular-nums">{formatDate(a.created_at)}</span>
          <span className="font-mono text-[12.5px] text-ink">{a.action}</span>
          {a.company && <span className="text-ink-2">{a.company}</span>}
          <span className="text-ink-3">{a.staff_email ?? (a.action.startsWith('provisioner') ? 'провизионер' : '')}</span>
          {Object.keys(a.details).length > 0 && (
            <span className="w-full truncate font-mono text-[12px] text-ink-3">{JSON.stringify(a.details)}</span>
          )}
        </li>
      ))}
    </ul>
  )
}

export function ApplicationView() {
  const { id } = useParams()
  const { staff, logout } = useStaff()
  const [app, setApp] = useState<ApplicationDetail | null>(null)
  const [config, setConfig] = useState<StaffConfig | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const [confirmSlug, setConfirmSlug] = useState('')
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setApp(await api.get<ApplicationDetail>(`/api/staff/applications/${id}`))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Ошибка загрузки')
    }
  }, [id])

  useEffect(() => {
    if (!staff) return
    void load()
    api.get<StaffConfig>('/api/staff/config').then(setConfig).catch(() => undefined)
  }, [staff, load])

  useEffect(() => {
    if (!app || !['approved', 'provisioning'].includes(app.status) && !app.jobs.some((j) => j.status === 'pending' || j.status === 'claimed')) return
    const t = setInterval(() => void load(), 5000)
    return () => clearInterval(t)
  }, [app, load])

  async function act(action: string, body: unknown, done: string) {
    setBusy(action)
    setError(null)
    setNotice(null)
    try {
      await api.post(`/api/staff/applications/${id}/${action}`, body)
      setNotice(done)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось выполнить действие')
    } finally {
      setBusy(null)
    }
  }

  const openJob = app?.jobs.find((j) => j.status === 'pending' || j.status === 'claimed')

  return (
    <Shell subtitle="Заявка" home="/staff" user={staff?.email} onLogout={logout} wide>
      <Link to="/staff" className="inline-flex items-center gap-1 text-[13.5px] font-semibold text-ink-2 hover:text-accent">
        <ArrowLeft className="size-4" aria-hidden /> Все заявки
      </Link>
      {error && (
        <div className="mt-4">
          <Alert>{error}</Alert>
        </div>
      )}
      {notice && (
        <div className="mt-4">
          <Alert tone="ok">{notice}</Alert>
        </div>
      )}
      {app && (
        <div className="mt-4 grid gap-5 lg:grid-cols-[1fr_380px]">
          <div className="flex flex-col gap-5">
            <Card className="p-5">
              <div className="flex flex-wrap items-center gap-3">
                <h1 className="text-[22px] font-extrabold tracking-tight text-ink">{app.company}</h1>
                <StatusBadge status={app.status} />
              </div>
              <dl className="mt-4 grid gap-x-6 gap-y-3 text-[14px] sm:grid-cols-2">
                {[
                  ['Контактное лицо', app.contact_name],
                  ['E-mail', app.email],
                  ['Телефон', app.phone],
                  ['Адрес бота', app.bot_host],
                  ['Лимит коинов', app.coins_limit === null ? '—' : app.coins_limit === 0 ? 'без лимита' : app.coins_limit.toLocaleString('ru-RU')],
                  ['Порт на сервере', app.port ?? '—'],
                  ['Подана', formatDate(app.created_at)],
                  ['E-mail подтверждён', formatDate(app.email_verified_at)],
                  ['IP при регистрации', app.register_ip ?? '—'],
                ].map(([k, v]) => (
                  <div key={String(k)}>
                    <dt className="text-[12.5px] font-semibold text-ink-3">{k}</dt>
                    <dd className="break-words text-ink">{v}</dd>
                  </div>
                ))}
                {app.admin_url && (
                  <div>
                    <dt className="text-[12.5px] font-semibold text-ink-3">Панель бота</dt>
                    <dd>
                      <a href={app.admin_url} target="_blank" rel="noopener noreferrer" className="text-accent hover:text-accent-hover">
                        {app.admin_url}
                      </a>
                    </dd>
                  </div>
                )}
              </dl>
              {app.reject_reason && app.status === 'rejected' && (
                <div className="mt-4">
                  <Alert tone="warn">Причина отказа: {app.reject_reason}</Alert>
                </div>
              )}
              {app.last_error && (
                <div className="mt-4">
                  <Alert>Последняя ошибка провизионера: {app.last_error}</Alert>
                </div>
              )}
            </Card>

            <Section title="Задания провизионера">
              {app.jobs.length === 0 ? (
                <p className="text-[13.5px] text-ink-3">Заданий нет</p>
              ) : (
                <ul className="flex flex-col divide-y divide-line text-[13.5px]">
                  {app.jobs.map((j) => (
                    <li key={j.id} className="py-2">
                      <div className="flex flex-wrap gap-x-3">
                        <span className="font-semibold text-ink">{JOB_KIND[j.kind] ?? j.kind}</span>
                        <span className={j.status === 'failed' ? 'text-danger' : j.status === 'done' ? 'text-ok' : 'text-warn'}>
                          {JOB_STATUS[j.status] ?? j.status}
                        </span>
                        <span className="text-ink-3 tabular-nums">{formatDate(j.created_at)}</span>
                        {j.has_custom_key && <span className="text-ink-3">ключ клиента</span>}
                      </div>
                      {j.error && <pre className="mt-1 whitespace-pre-wrap font-mono text-[12px] text-danger">{j.error}</pre>}
                    </li>
                  ))}
                </ul>
              )}
            </Section>

            <Section title="Журнал">
              <AuditList rows={app.audit} />
            </Section>
          </div>

          <div className="flex flex-col gap-5">
            {openJob && <Alert tone="info">Выполняется: {JOB_KIND[openJob.kind] ?? openJob.kind}. Страница обновляется сама.</Alert>}
            {['verified', 'rejected'].includes(app.status) && config && (
              <Section title="Одобрить">
                <ApproveForm applicationId={app.id} defaultSlug={app.slug} config={config} onDone={() => { setNotice('Заявка одобрена, бот ставится в очередь на создание'); void load() }} />
              </Section>
            )}
            {['verified', 'approved', 'failed'].includes(app.status) && (
              <Section title="Отклонить">
                <Field label="Причина (уйдёт клиенту письмом)">
                  <textarea
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    rows={3}
                    maxLength={1000}
                    className="w-full rounded-nx border border-line-strong bg-raised px-3 py-2 text-[14px] text-ink focus:border-accent focus:outline-none"
                  />
                </Field>
                <Button
                  className="mt-3"
                  variant="danger"
                  loading={busy === 'reject'}
                  disabled={reason.trim().length < 3}
                  onClick={() => act('reject', { reason }, 'Заявка отклонена, клиенту отправлено письмо')}
                >
                  Отклонить заявку
                </Button>
              </Section>
            )}
            {app.status === 'failed' && (
              <Section title="Ошибка создания">
                <p className="text-[13.5px] text-ink-2">Исправьте причину на сервере (journalctl -u nox-provisioner) и повторите.</p>
                <Button className="mt-3" variant="primary" loading={busy === 'retry'} onClick={() => act('retry', {}, 'Повтор поставлен в очередь')}>
                  Повторить создание
                </Button>
              </Section>
            )}
            {['active', 'suspended'].includes(app.status) && (
              <Section title="Управление ботом">
                {app.status === 'active' ? (
                  <Button loading={busy === 'suspend'} disabled={!!openJob} onClick={() => act('suspend', {}, 'Приостановка поставлена в очередь')}>
                    Приостановить
                  </Button>
                ) : (
                  <Button variant="primary" loading={busy === 'resume'} disabled={!!openJob} onClick={() => act('resume', {}, 'Возобновление поставлено в очередь')}>
                    Возобновить
                  </Button>
                )}
              </Section>
            )}
            {['active', 'suspended', 'failed'].includes(app.status) && (
              <Section title="Удалить бота">
                <p className="text-[13.5px] text-ink-2">
                  Удаляются контейнеры, база, сессия WhatsApp, резервные копии и адрес в туннеле. Отменить нельзя.
                </p>
                <Field label={`Введите адрес «${app.slug}» для подтверждения`} className="mt-3">
                  <Input value={confirmSlug} onChange={(e) => setConfirmSlug(e.target.value)} spellCheck={false} />
                </Field>
                <Button
                  className="mt-3"
                  variant="danger"
                  loading={busy === 'remove'}
                  disabled={confirmSlug.trim() !== app.slug || !!openJob}
                  onClick={() => act('remove', { confirm_slug: confirmSlug.trim() }, 'Удаление поставлено в очередь')}
                >
                  Удалить навсегда
                </Button>
              </Section>
            )}
          </div>
        </div>
      )}
    </Shell>
  )
}
