import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router'
import { CheckCircle2, Clock, Copy, CreditCard, ExternalLink, FileText, FlaskConical, Hourglass, KeyRound, PauseCircle, Receipt, XCircle } from 'lucide-react'
import { api, ApiError, formatDate, formatDay, FREE_CODE, money, type ClientMe, type InvoiceListItem, type Tariff } from '../api'
import { Shell } from '../components/Shell'
import { Alert, Button, Card, StatusBadge } from '../components/ui'
import { CoinsPanel } from './CoinsPanel'
import { PayOnlineButton } from './PayOnline'
import { TariffPicker } from './TariffPicker'

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

function useTariffName(code: string | null | undefined): string {
  const [tariffs, setTariffs] = useState<Tariff[]>([])
  useEffect(() => {
    if (!code) return
    api.get<Tariff[]>('/api/tariffs').then(setTariffs).catch(() => undefined)
  }, [code])
  return tariffs.find((t) => t.code === code)?.name ?? code ?? ''
}

function ChangeChoice({ me, onChange, label, upgrade }: { me: ClientMe; onChange: (me: ClientMe) => void; label: string; upgrade?: boolean }) {
  const [open, setOpen] = useState(false)
  if (!open) {
    return (
      <Button variant="ghost" onClick={() => setOpen(true)}>
        {label}
      </Button>
    )
  }
  return (
    <div className="mt-4 w-full">
      <TariffPicker
        current={me.tariff}
        upgrade={upgrade}
        onChosen={(next) => {
          setOpen(false)
          onChange(next)
        }}
      />
    </div>
  )
}

function AwaitingInvoice({ me, onChange }: { me: ClientMe; onChange: (me: ClientMe) => void }) {
  const name = useTariffName(me.tariff)
  return (
    <StateBlock icon={<Receipt className="size-9 text-warn" />} title="Менеджер свяжется с вами и выставит счёт">
      <p>
        Вы выбрали тариф «{name}» и оплату по счёту. Счёт придёт на {me.email} и появится здесь, оплатить его можно банковским
        переводом. После оплаты мы создадим бота и пришлём ссылку на панель.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <ChangeChoice me={me} onChange={onChange} label="Сменить тариф или способ оплаты" />
      </div>
    </StateBlock>
  )
}

function PaymentPending({ me, onChange }: { me: ClientMe; onChange: (me: ClientMe) => void }) {
  const name = useTariffName(me.tariff)
  return (
    <StateBlock icon={<CreditCard className="size-9 text-warn" />} title="Ожидаем онлайн-оплату">
      <p>
        Тариф «{name}». Как только платёж подтвердится, бот встанет в очередь на создание — страница обновится сама.
        {!me.payment_url && ' Предыдущая попытка не прошла: оплатите ещё раз или выберите оплату по счёту.'}
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {me.payment_url && (
          <a
            href={me.payment_url}
            className="inline-flex h-10 items-center gap-1.5 rounded-nx bg-accent px-4 text-sm font-semibold text-white hover:bg-accent-hover"
          >
            <CreditCard className="size-4" aria-hidden /> Перейти к оплате
          </a>
        )}
        <ChangeChoice me={me} onChange={onChange} label="Выбрать другой способ" />
      </div>
    </StateBlock>
  )
}

function AwaitingPayment({ me, onChange }: { me: ClientMe; onChange: (me: ClientMe) => void }) {
  return (
    <StateBlock icon={<Receipt className="size-9 text-warn" />} title="Ожидаем оплату">
      <p>
        Счёт № {me.invoice_no} на {me.invoice_amount !== null ? money(me.invoice_amount) : '—'} отправлен на {me.email}. Оплатите
        его банковским переводом — после поступления оплаты мы создадим бота и пришлём ссылку на панель.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Link
          to="/invoice"
          className="inline-flex h-10 items-center gap-1.5 rounded-nx bg-accent px-4 text-sm font-semibold text-white hover:bg-accent-hover"
        >
          <FileText className="size-4" aria-hidden /> Открыть счёт (PDF)
        </Link>
        {me.invoice_no && <PayOnlineButton number={me.invoice_no} />}
        <ChangeChoice me={me} onChange={onChange} label="Сменить тариф" />
      </div>
    </StateBlock>
  )
}

/** A Free account: trial end, then the way to a paid tariff with the same bot. */
function FreeUpgrade({ me, onChange }: { me: ClientMe; onChange: (me: ClientMe) => void }) {
  const [invoice, setInvoice] = useState<InvoiceListItem | null>(null)
  const wanted = useTariffName(me.upgrade_tariff)
  useEffect(() => {
    api
      .get<InvoiceListItem[]>('/api/me/invoices')
      .then((list) => setInvoice(list.find((i) => i.kind === 'upgrade' && !i.paid) ?? null))
      .catch(() => undefined)
  }, [me.upgrade_tariff])
  return (
    <div className="mt-4 rounded-nx border border-line bg-sunken p-4" data-testid="free-upgrade">
      {invoice ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[14px] text-ink">
            Счёт № {invoice.number} на {money(invoice.amount_kzt)} — переход на платный тариф.
          </span>
          <Link to={`/invoice/${encodeURIComponent(invoice.number)}`} className="font-semibold text-accent hover:text-accent-hover">
            Открыть счёт
          </Link>
          <PayOnlineButton number={invoice.number} size="sm" />
        </div>
      ) : me.upgrade_tariff ? (
        <p className="text-[14px] text-ink">Менеджер свяжется с вами и выставит счёт на тариф «{wanted}».</p>
      ) : (
        <p className="text-[14px] text-ink">Чтобы продолжить с тем же ботом, выберите платный тариф.</p>
      )}
      <div className="mt-2">
        <ChangeChoice me={me} onChange={onChange} label={me.upgrade_tariff || invoice ? 'Выбрать другой тариф' : 'Выбрать тариф'} upgrade />
      </div>
    </div>
  )
}

function StatusView({ me, reload, setMe }: { me: ClientMe; reload: () => void; setMe: (me: ClientMe) => void }) {
  const icon = 'size-9'
  const free = me.tariff === FREE_CODE
  switch (me.status) {
    case 'verified':
      return (
        <StateBlock icon={<Hourglass className={`${icon} text-accent`} />} title="Выберите тариф">
          <p className="mb-4">E-mail подтверждён. Выберите тариф и способ оплаты — или подключите бесплатный тест.</p>
          <TariffPicker onChosen={setMe} />
        </StateBlock>
      )
    case 'awaiting_invoice':
      return <AwaitingInvoice me={me} onChange={setMe} />
    case 'payment_pending':
      return <PaymentPending me={me} onChange={setMe} />
    case 'awaiting_payment':
      return <AwaitingPayment me={me} onChange={setMe} />
    case 'approved':
      return (
        <StateBlock icon={<Hourglass className={`${icon} text-accent`} />} title={free ? 'Тест подключается' : 'Оплата получена'}>
          {free ? 'Бот на тестовом тарифе' : 'Спасибо! Бот'} поставлен в очередь на создание. Как только он будет готов, придёт письмо на{' '}
          {me.email}.
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
          {free && (
            <>
              <p className="mt-3 inline-flex items-center gap-1.5 text-[13.5px] font-semibold text-warn">
                <FlaskConical className="size-4" aria-hidden /> Тестовый тариф до {formatDay(me.free_until)}
              </p>
              <FreeUpgrade me={me} onChange={setMe} />
            </>
          )}
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
      if (me.suspend_reason === 'trial_ended') {
        return (
          <StateBlock icon={<PauseCircle className={`${icon} text-warn`} />} title="Тестовый период закончился">
            <p>Бот приостановлен, настройки и данные сохранены. После оплаты платного тарифа он продолжит работу.</p>
            <FreeUpgrade me={me} onChange={setMe} />
          </StateBlock>
        )
      }
      return (
        <StateBlock icon={<PauseCircle className={`${icon} text-warn`} />} title="Бот приостановлен">
          {me.suspend_reason === 'unpaid'
            ? 'Счёт за продление не оплачен. Данные сохранены — после оплаты бот снова заработает (счёт — ниже, в разделе «Коины»).'
            : 'Данные сохранены. Чтобы возобновить работу, свяжитесь с Nox service.'}
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
    if (!me || !['approved', 'provisioning', 'payment_pending'].includes(me.status)) return
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
            <StatusView me={me} reload={load} setMe={setMe} />
          </Card>
          {me.llm_enabled && !['removed', 'rejected'].includes(me.status) && <CoinsPanel />}
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
