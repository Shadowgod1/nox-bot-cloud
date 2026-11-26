import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { CheckCircle2, Clock, Copy, ExternalLink, Hourglass, KeyRound, PauseCircle, XCircle } from 'lucide-react'
import { api, ApiError, formatDate, type ClientMe } from '../api'
import { Shell } from '../components/Shell'
import { Alert, Button, Card, StatusBadge } from '../components/ui'

function StateBlock({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex gap-4">
      <div className="mt-0.5 shrink-0">{icon}</div>
      <div className="min-w-0">
        <h2 className="text-[19px] font-extrabold tracking-tight text-ink">{title}</h2>
        <div className="mt-1.5 text-[14.5px] text-ink-2">{children}</div>
      </div>
    </div>
  )
}

function SetupLink({ onRevealed }: { onRevealed: () => void }) {
  const [url, setUrl] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  async function reveal() {
    setBusy(true)
    setError(null)
    try {
      const r = await api.post<{ setup_url: string }>('/api/me/setup-link')
      setUrl(r.setup_url)
      onRevealed()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось получить ссылку.')
    } finally {
      setBusy(false)
    }
  }

  if (url) {
    return (
      <div className="mt-4 rounded-nx border border-warn/40 bg-warn-soft p-4">
        <p className="text-[13.5px] font-semibold text-warn">Ссылка показывается один раз. Сохраните её или сразу откройте.</p>
        <div className="mt-2 flex items-center gap-2">
          <code className="min-w-0 flex-1 truncate rounded-nx bg-raised px-2 py-1.5 font-mono text-[12.5px] text-ink">{url}</code>
          <Button
            size="sm"
            icon={<Copy className="size-4" aria-hidden />}
            onClick={() => {
              void navigator.clipboard?.writeText(url).then(() => setCopied(true))
            }}
          >
            {copied ? 'Скопировано' : 'Копировать'}
          </Button>
        </div>
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-3 inline-flex items-center gap-1.5 text-[14px] font-semibold text-accent hover:text-accent-hover"
        >
          Создать учётную запись владельца <ExternalLink className="size-4" aria-hidden />
        </a>
      </div>
    )
  }
  return (
    <div className="mt-4">
      <p className="text-[13.5px] text-ink-2">
        Первый вход в панель бота — по одноразовой ссылке: она создаёт учётную запись владельца. Копия ссылки отправлена на
        вашу почту.
      </p>
      <Button className="mt-3" icon={<KeyRound className="size-4" aria-hidden />} loading={busy} onClick={reveal}>
        Показать ссылку для первого входа
      </Button>
      {error && (
        <div className="mt-3">
          <Alert>{error}</Alert>
        </div>
      )}
    </div>
  )
}

function StatusView({ me, reload }: { me: ClientMe; reload: () => void }) {
  const icon = 'size-9'
  switch (me.status) {
    case 'verified':
    case 'approved':
      return (
        <StateBlock icon={<Hourglass className={`${icon} text-accent`} />} title="Заявка на рассмотрении">
          Мы проверим заявку и свяжемся с вами в рабочее время. Как только бот будет готов, придёт письмо на {me.email}.
        </StateBlock>
      )
    case 'provisioning':
    case 'failed':
      return (
        <StateBlock icon={<Clock className={`${icon} text-warn`} />} title="Бот создаётся">
          Заявка одобрена, готовим вашего бота. Обычно это занимает несколько минут — письмо придёт, как только всё будет
          готово.
        </StateBlock>
      )
    case 'active':
      return (
        <StateBlock icon={<CheckCircle2 className={`${icon} text-ok`} />} title="Ваш бот готов">
          <p>
            Панель управления:{' '}
            <a href={me.admin_url ?? '#'} target="_blank" rel="noopener noreferrer" className="font-semibold text-accent hover:text-accent-hover">
              {me.admin_url}
            </a>
          </p>
          {me.setup_link_available && <SetupLink onRevealed={reload} />}
        </StateBlock>
      )
    case 'rejected':
      return (
        <StateBlock icon={<XCircle className={`${icon} text-danger`} />} title="Заявка отклонена">
          {me.reject_reason && <p>Причина: {me.reject_reason}</p>}
          <p className="mt-1">Если есть вопросы — ответьте на наше письмо или позвоните нам.</p>
        </StateBlock>
      )
    case 'suspended':
      return (
        <StateBlock icon={<PauseCircle className={`${icon} text-warn`} />} title="Бот приостановлен">
          Данные сохранены. Чтобы возобновить работу, свяжитесь с Nox service.
        </StateBlock>
      )
    case 'removed':
      return (
        <StateBlock icon={<XCircle className={`${icon} text-ink-3`} />} title="Бот отключён">
          Бот удалён. Чтобы подключить его снова, свяжитесь с Nox service.
        </StateBlock>
      )
    default:
      return null
  }
}

export function Cabinet() {
  const navigate = useNavigate()
  const [me, setMe] = useState<ClientMe | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setMe(await api.get<ClientMe>('/api/me'))
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) navigate('/login', { replace: true })
      else setError(err instanceof Error ? err.message : 'Не удалось загрузить данные.')
    }
  }, [navigate])

  useEffect(() => {
    void load()
  }, [load])

  // refresh while the bot is being prepared
  useEffect(() => {
    if (!me || !['approved', 'provisioning'].includes(me.status)) return
    const t = setInterval(() => void load(), 15000)
    return () => clearInterval(t)
  }, [me, load])

  async function logout() {
    await api.post('/api/auth/logout').catch(() => undefined)
    navigate('/login', { replace: true })
  }

  return (
    <Shell subtitle="Личный кабинет" user={me?.email} onLogout={logout}>
      {error && <Alert>{error}</Alert>}
      {me && (
        <div className="flex flex-col gap-5">
          <Card className="p-6">
            <StatusView me={me} reload={load} />
          </Card>
          <Card className="p-6">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-[15px] font-bold text-ink">Заявка</h2>
              <StatusBadge status={me.status} />
            </div>
            <dl className="mt-4 grid gap-x-6 gap-y-3 text-[14px] sm:grid-cols-2">
              {[
                ['Компания', me.company],
                ['Контактное лицо', me.contact_name],
                ['E-mail', me.email],
                ['Телефон', me.phone],
                ['Адрес бота', me.bot_host],
                ['Подана', formatDate(me.created_at)],
              ].map(([k, v]) => (
                <div key={k}>
                  <dt className="text-[12.5px] font-semibold text-ink-3">{k}</dt>
                  <dd className="text-ink">{v}</dd>
                </div>
              ))}
            </dl>
          </Card>
        </div>
      )}
    </Shell>
  )
}
