import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArchiveRestore, Download, HardDriveDownload, LogOut, RefreshCw, Trash2, UserPlus } from 'lucide-react'
import { api } from '../../api/client'
import type {
  BackupInfo,
  BackupList,
  Ok,
  RestoreResult,
  Role,
  UpdateState,
  UpdateStatus,
  User,
  UserCreate,
  UserListItem,
  VersionInfo,
} from '../../api/types'
import { useBrand } from '../../brand'
import { useAuth } from '../../hooks/useAuth'
import { useLoad } from '../../hooks/useLoad'
import { PageBody } from '../../components/Layout'
import { Badge, Button, EmptyState, ErrorState, Field, Input, PageHeader, Panel, Select, Spinner } from '../../components/ui'
import { toast } from '../../components/toast'
import { formatBytes, formatDateTime, ru } from '../../i18n/ru'
import { LlmSettingsPanel } from './LlmSettings'

export function System() {
  const { isOwner } = useAuth()
  return (
    <PageBody>
      <PageHeader
        title="Система"
        description={isOwner ? 'Модель, пользователи, обновления и резервные копии.' : 'Ваша учётная запись. Остальные разделы доступны владельцу.'}
      />
      {/* 2xl+: the model settings on the left; users, updates, backups and the account on the right */}
      <div className="grid items-start gap-5 2xl:grid-cols-2">
        {isOwner && (
          <div className="min-w-0">
            <LlmSettingsPanel />
          </div>
        )}
        <div className="flex min-w-0 flex-col gap-5">
          {isOwner && (
            <>
              <UsersPanel />
              <UpdatePanel />
              <BackupsPanel />
            </>
          )}
          <AccountPanel />
        </div>
      </div>
    </PageBody>
  )
}

/* ---------- Users ---------- */

function UsersPanel() {
  const { user: me } = useAuth()
  const list = useLoad(() => api.get<UserListItem[]>('/api/users', { silent: true }))
  const [adding, setAdding] = useState(false)

  async function remove(u: UserListItem) {
    if (!window.confirm(`Удалить пользователя ${u.email}? Он сразу потеряет доступ к админке.`)) return
    try {
      await api.del<Ok>(`/api/users/${u.id}`)
      list.setData((d) => d?.filter((x) => x.id !== u.id))
      toast.success('Пользователь удалён')
    } catch {
      /* toast */
    }
  }

  return (
    <Panel
      title="Пользователи"
      description="Владелец управляет всем, оператор работает с диалогами и заявками."
      bodyClassName="p-0"
      actions={
        !adding && (
          <Button size="sm" icon={<UserPlus className="size-3.5" aria-hidden />} onClick={() => setAdding(true)}>
            Добавить
          </Button>
        )
      }
    >
      {adding && (
        <NewUserForm
          onCancel={() => setAdding(false)}
          onCreated={(u) => {
            // POST /api/users returns the bare User; fill the list-only fields locally.
            list.setData((d) => [...(d ?? []), { ...u, created_at: new Date().toISOString(), last_login_at: null }])
            setAdding(false)
          }}
        />
      )}
      {list.loading && !list.data ? (
        <Spinner />
      ) : list.error && !list.data ? (
        <ErrorState message={list.error.message} onRetry={list.reload} />
      ) : !list.data?.length ? (
        <EmptyState title="Пользователей нет" />
      ) : (
        <ul className="divide-y divide-line">
          {list.data.map((u) => (
            <li key={u.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 sm:px-5">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-ink">
                  {u.name || u.email}
                  {u.id === me?.id && <span className="ml-1.5 font-normal text-ink-3">(вы)</span>}
                </p>
                <p className="truncate text-[13px] text-ink-2">
                  {u.email}
                  {u.last_login_at && <span className="text-ink-3"> · вход {formatDateTime(u.last_login_at)}</span>}
                </p>
              </div>
              <Badge tone={u.role === 'owner' ? 'accent' : 'neutral'}>{ru.roles[u.role]}</Badge>
              {u.id !== me?.id && (
                <Button size="sm" variant="danger-ghost" aria-label={`Удалить ${u.email}`} onClick={() => remove(u)} icon={<Trash2 className="size-3.5" aria-hidden />} />
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}

function NewUserForm({ onCancel, onCreated }: { onCancel: () => void; onCreated: (u: User) => void }) {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<Role>('operator')
  const [busy, setBusy] = useState(false)
  const short = password.length > 0 && password.length < 10

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (short || !email.trim()) return
    setBusy(true)
    try {
      const body: UserCreate = { name: name.trim(), email: email.trim(), password, role }
      const u = await api.post<User>('/api/users', body)
      toast.success('Пользователь создан. Передайте ему пароль лично.')
      onCreated(u)
    } catch {
      /* toast */
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-4 border-b border-line bg-sunken/60 px-5 py-4 md:grid-cols-2">
      <Field label="Имя">
        <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
      </Field>
      <Field label="E-mail">
        <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="off" required />
      </Field>
      <Field label="Пароль" hint="Не короче 10 символов" error={short ? 'Не короче 10 символов' : null}>
        <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" required />
      </Field>
      <Field label="Роль">
        <Select value={role} onChange={(e) => setRole(e.target.value as Role)}>
          <option value="operator">Оператор</option>
          <option value="owner">Владелец</option>
        </Select>
      </Field>
      <div className="flex gap-2 md:col-span-2">
        <Button type="submit" variant="primary" loading={busy} disabled={!email.trim() || password.length < 10}>
          Создать пользователя
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          {ru.common.cancel}
        </Button>
      </div>
    </form>
  )
}

/* ---------- Update ---------- */

const ACTIVE: UpdateState[] = ['queued', 'backup', 'pulling', 'restarting']
export const UPDATE_POLL_MS = 2000

const isActive = (s: UpdateState | string | null | undefined) => !!s && (ACTIVE as string[]).includes(s)

function UpdatePanel() {
  const brand = useBrand()
  const version = useLoad(() => api.get<VersionInfo>('/api/system/version', { silent: true }))
  const [status, setStatus] = useState<UpdateStatus | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const active = isActive(status?.state)
  // True while an update is (or was just seen) running: only then do failed polls keep retrying.
  const running = useRef(false)

  async function poll(id: string | null) {
    try {
      // Without job_id core reports the last persisted job ({job_id: null, state: null} if none).
      const s = await api.get<UpdateStatus>(`/api/system/update/status${id ? `?job_id=${encodeURIComponent(id)}` : ''}`, { silent: true, noAuthRedirect: true })
      setStatus(s)
      if (s.job_id && !id) setJobId((j) => j ?? (isActive(s.state) ? s.job_id : null))
      running.current = isActive(s.state)
      if (running.current) {
        timer.current = setTimeout(() => void poll(id ?? s.job_id), UPDATE_POLL_MS)
      } else if (s.state === 'healthy') {
        void version.reload()
      }
    } catch {
      // Core / updater restart during an update: keep polling until it is back. Otherwise stay quiet.
      if (running.current) timer.current = setTimeout(() => void poll(id), UPDATE_POLL_MS * 2)
    }
  }

  useEffect(() => {
    void poll(null)
    return () => clearTimeout(timer.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function start() {
    const v = version.data
    if (!window.confirm(`Обновить ${brand.name}${v?.available ? ` до версии ${v.available}` : ''}? Перед обновлением создаётся резервная копия. Бот будет недоступен 1–3 минуты.`)) return
    setStarting(true)
    try {
      const { job_id: id } = await api.post<{ job_id: string }>('/api/system/update')
      setJobId(id)
      setStatus({ job_id: id, state: 'queued', log: '' })
      running.current = true
      clearTimeout(timer.current)
      timer.current = setTimeout(() => void poll(id), UPDATE_POLL_MS)
    } catch {
      /* toast */
    } finally {
      setStarting(false)
    }
  }

  const v = version.data
  const tone = status?.state === 'healthy' ? 'ok' : status?.state === 'failed' || status?.state === 'rolled_back' ? 'danger' : 'warn'

  return (
    <Panel title="Версия и обновление" description="Обновления устанавливаются только по кнопке. Если новая версия не запустится, система вернётся к предыдущей.">
      {version.loading && !v ? (
        <Spinner />
      ) : version.error && !v ? (
        <ErrorState message={version.error.message} onRetry={version.reload} />
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <dl className="flex flex-wrap gap-x-8 gap-y-1 text-sm">
              <div>
                <dt className="text-[13px] text-ink-2">Установлена</dt>
                <dd className="tabular font-medium text-ink">{v?.current ?? v?.core_version ?? '—'}</dd>
              </div>
              <div>
                <dt className="text-[13px] text-ink-2">Доступна</dt>
                <dd className="tabular font-medium text-ink">
                  {v && !v.updater_available ? 'Неизвестно' : (v?.available ?? 'Последняя версия')}
                </dd>
              </div>
            </dl>
            <Button
              variant={v?.available ? 'primary' : 'secondary'}
              onClick={start}
              loading={starting}
              disabled={active || !v?.available || !v.updater_available}
              icon={<RefreshCw className="size-4" aria-hidden />}
            >
              {active ? 'Обновляется…' : 'Обновить'}
            </Button>
          </div>
          {v && !v.updater_available && (
            <p className="text-[13px] text-ink-2">Сервис обновлений недоступен: проверка и установка обновлений сейчас невозможны.</p>
          )}
          {status?.state && (jobId || active || status.state === 'failed' || status.state === 'rolled_back') && (
            <div className="rounded-[9px] border border-line bg-sunken p-3.5" role="status" aria-live="polite">
              <div className="flex items-center gap-2">
                <Badge tone={tone} dot>
                  {ru.updateState[status.state] ?? status.state}
                </Badge>
                {status.state === 'healthy' && (
                  <Button size="sm" variant="ghost" onClick={() => window.location.reload()}>
                    Перезагрузить страницу
                  </Button>
                )}
              </div>
              {status.log && (
                <pre className="mt-3 max-h-56 overflow-auto font-mono text-[12px] leading-relaxed whitespace-pre-wrap text-ink-2">{status.log}</pre>
              )}
            </div>
          )}
        </div>
      )}
    </Panel>
  )
}

/* ---------- Backups ---------- */

function BackupsPanel() {
  const list = useLoad(() => api.get<BackupList>('/api/backups', { silent: true }).then((r) => r.items))
  const [creating, setCreating] = useState(false)
  const [restoring, setRestoring] = useState<string | null>(null)

  async function create() {
    setCreating(true)
    try {
      const b = await api.post<BackupInfo>('/api/backups')
      list.setData((d) => [b, ...(d ?? []).filter((x) => x.name !== b.name)])
      toast.success('Резервная копия создана')
    } catch {
      /* toast */
    } finally {
      setCreating(false)
    }
  }

  async function restore(b: BackupInfo) {
    const ok = window.confirm(
      `Восстановить данные из копии от ${formatDateTime(b.created_at)}?\n\nВсе диалоги, заявки и настройки после этого момента будут заменены данными из копии. Перед восстановлением автоматически создаётся страховочная копия текущих данных.`,
    )
    if (!ok) return
    setRestoring(b.name)
    try {
      // Synchronous: core answers once the databases are restored.
      const r = await api.post<RestoreResult>(`/api/backups/${encodeURIComponent(b.name)}/restore`)
      toast.success(`Данные восстановлены. Страховочная копия: ${r.safety_backup}. Страница сейчас обновится.`)
      setTimeout(() => window.location.reload(), 4000)
    } catch {
      /* toast */
    } finally {
      setRestoring(null)
    }
  }

  return (
    <Panel
      title="Резервные копии"
      description="Создаются автоматически каждый день в 04:00 и хранятся 14 дней. В копию входят база данных и сессия WhatsApp."
      bodyClassName="p-0"
      actions={
        <Button size="sm" loading={creating} onClick={create} icon={<HardDriveDownload className="size-3.5" aria-hidden />}>
          Создать копию
        </Button>
      }
    >
      {list.loading && !list.data ? (
        <Spinner />
      ) : list.error && !list.data ? (
        <ErrorState message={list.error.message} onRetry={list.reload} />
      ) : !list.data?.length ? (
        <EmptyState title="Копий пока нет">Первая копия появится сегодня ночью, или создайте её сейчас.</EmptyState>
      ) : (
        <ul className="divide-y divide-line">
          {list.data.map((b) => (
            <li key={b.name} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 sm:px-5">
              {/* phones: the date gets its own line, size and buttons go below */}
              <div className="min-w-0 flex-1 max-sm:basis-full">
                <p className="text-sm font-medium text-ink">{formatDateTime(b.created_at)}</p>
                <p className="truncate font-mono text-[12px] text-ink-3" title={b.name}>
                  {b.name}
                </p>
              </div>
              <span className="tabular text-[13px] text-ink-2">{formatBytes(b.size)}</span>
              <a
                href={`/api/backups/${encodeURIComponent(b.name)}`}
                download={b.name}
                className="inline-flex h-8 items-center gap-1.5 rounded-[7px] px-2.5 text-[13px] font-medium text-ink-2 hover:bg-sunken hover:text-ink"
              >
                <Download className="size-3.5" aria-hidden /> Скачать
              </a>
              <Button size="sm" variant="ghost" loading={restoring === b.name} disabled={!!restoring} onClick={() => restore(b)} icon={<ArchiveRestore className="size-3.5" aria-hidden />}>
                Восстановить
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}

/* ---------- Account ---------- */

function AccountPanel() {
  const { user, logout } = useAuth()
  const [oldPw, setOldPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [busy, setBusy] = useState(false)
  const short = newPw.length > 0 && newPw.length < 10

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (newPw.length < 10 || !oldPw) return
    setBusy(true)
    try {
      await api.post('/api/auth/password', { old: oldPw, new: newPw })
      toast.success('Пароль изменён')
      setOldPw('')
      setNewPw('')
    } catch {
      /* toast */
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel
      title="Учётная запись"
      description={user ? `${user.email} · ${ru.roles[user.role]}` : undefined}
      actions={
        <Button size="sm" variant="ghost" onClick={() => void logout()} icon={<LogOut className="size-3.5" aria-hidden />}>
          Выйти
        </Button>
      }
    >
      <form onSubmit={submit} className="grid gap-4 md:grid-cols-[1fr_1fr_auto] md:items-start">
        <Field label="Текущий пароль">
          <Input type="password" autoComplete="current-password" value={oldPw} onChange={(e) => setOldPw(e.target.value)} />
        </Field>
        <Field label="Новый пароль" hint="Не короче 10 символов" error={short ? 'Не короче 10 символов' : null}>
          <Input type="password" autoComplete="new-password" value={newPw} onChange={(e) => setNewPw(e.target.value)} />
        </Field>
        <Button type="submit" loading={busy} disabled={!oldPw || newPw.length < 10} className="md:mt-[26px]">
          Сменить пароль
        </Button>
      </form>
    </Panel>
  )
}
