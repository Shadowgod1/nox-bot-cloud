import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router'
import {
  CheckCircle2, ChevronDown, Clock, Copy, CreditCard, ExternalLink, FileText, FlaskConical, Headset, Hourglass, KeyRound,
  LayoutDashboard, PauseCircle, Receipt, XCircle,
} from 'lucide-react'
import {
  api, ApiError, daysLeft, formatDate, formatDay, FREE_CODE, money, plural, type ClientMe, type InvoiceListItem, type Tariff,
} from '../api'
import { Shell } from '../components/Shell'
import { SupportLinks } from '../components/Support'
import { Steps, stepsFor } from '../components/Steps'
import { Alert, Button, Card, StatusBadge } from '../components/ui'
import { CoinsPanel } from './CoinsPanel'
import { PaymentQr } from '../components/PaymentQr'
import { PayOnlineButton, usePublicConfig } from './PayOnline'
import { TariffPicker } from './TariffPicker'

function StateBlock({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex gap-4">
      {icon && <div className="mt-0.5 shrink-0">{icon}</div>}
      <div className="min-w-0 flex-1">
        <h2 className="text-[20px] font-extrabold tracking-[-0.015em] text-ink">{title}</h2>
        <div className="mt-1.5 text-[14.5px] text-ink-2">{children}</div>
      </div>
    </div>
  )
}

/** First login to the bot panel: the one-time owner link, or (once used) where to go when the password is lost. */
function SetupLink({ available }: { available: boolean }) {
  const support = usePublicConfig()?.support
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
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось получить ссылку.')
    } finally {
      setBusy(false)
    }
  }

  if (!url && !available) {
    return (
      <div className="mt-4 text-[13.5px] text-ink-2" data-testid="panel-password-help">
        <p>Забыли пароль от панели? Напишите в поддержку — поможем войти.</p>
        <SupportLinks support={support} email className="mt-1.5" />
      </div>
    )
  }

  if (url) {
    return (
      <div className="mt-4 rounded-nx border border-warn/40 bg-warn-soft p-4">
        <p className="text-[13.5px] font-semibold text-warn">
          Откройте ссылку и придумайте пароль. Здесь она больше не появится — копия есть в письме «Ваш бот готов».
        </p>
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
          Создать логин и пароль в панели <ExternalLink className="size-4" aria-hidden />
        </a>
      </div>
    )
  }
  return (
    <div className="mt-4 rounded-nx border border-line bg-sunken p-4">
      <p className="text-[14px] font-bold text-ink">Первый вход в панель</p>
      <p className="mt-1 text-[13.5px] text-ink-2">
        Ссылка создаёт ваш логин и пароль в панели. Работает один раз — дальше входите по паролю. Копия на почте.
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
  const config = usePublicConfig()
  const qr = config?.online_mode === 'qr' && !!me.payment_url
  return (
    <StateBlock icon={<CreditCard className="size-9 text-warn" />} title="Ожидаем онлайн-оплату">
      <p>
        Тариф «{name}». Как только платёж подтвердится, бот встанет в очередь на создание — страница обновится сама.
        {!me.payment_url && ' Предыдущая попытка не прошла: оплатите ещё раз или выберите оплату по счёту.'}
      </p>
      {qr && me.payment_url && <PaymentQr url={me.payment_url} amount={me.invoice_amount} number={me.invoice_no} />}
      <div className="mt-3 flex flex-wrap gap-2">
        {qr && me.invoice_no && (
          <Link to="/invoice" className="inline-flex h-10 items-center gap-1.5 rounded-nx border border-line-strong px-4 text-sm font-semibold text-ink hover:bg-sunken">
            <FileText className="size-4" aria-hidden /> Счёт (PDF) для банковского перевода
          </Link>
        )}
        {me.payment_url && !qr && (
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

/** Free: how long the test lasts and what happens after it. */
function TrialInfo({ me }: { me: ClientMe }) {
  const left = daysLeft(me.free_until)
  return (
    <div className="mt-4 rounded-nx border border-warn/40 bg-warn-soft/60 p-4" data-testid="trial-info">
      <p className="inline-flex items-center gap-1.5 text-[14px] font-bold text-ink">
        <FlaskConical className="size-4 text-warn" aria-hidden />
        {me.free_until
          ? left > 0
            ? `Бесплатный тест: ${left > 1 ? 'осталось' : 'остался'} ${left} ${plural(left, ['день', 'дня', 'дней'])}, до ${formatDay(me.free_until)}`
            : 'Бесплатный тест заканчивается'
          : 'Бесплатный тест'}
      </p>
      <p className="mt-1 text-[13.5px] text-ink-2">
        Когда тест закончится, бот перестанет отвечать клиентам. Настройки и переписка сохранятся, а после оплаты бот продолжит
        с того же места. За 3 дня и за день до конца пришлём письмо.
      </p>
    </div>
  )
}

/** "Настроим за вас": a manager connects WhatsApp and fills in services together with the client. No price here. */
function SetupHelp({ me, onChange }: { me: ClientMe; onChange: (me: ClientMe) => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const open = !!me.setup_help_requested_at && !me.setup_help_contacted_at

  async function ask() {
    setBusy(true)
    setError(null)
    try {
      onChange(await api.post<ClientMe>('/api/me/setup-help'))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось отправить запрос')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="p-6" data-testid="setup-help">
      <div className="flex gap-3">
        <Headset className="mt-0.5 size-6 flex-none text-accent" aria-hidden />
        <div className="min-w-0 flex-1">
          <h2 className="text-[17px] font-extrabold text-ink">Настроим за вас</h2>
          <p className="mt-1 text-[14px] text-ink-2">
            Не хотите разбираться? Менеджер подключит WhatsApp и заполнит услуги вместе с вами.
          </p>
          {open ? (
            <div className="mt-3">
              <Alert tone="ok">
                Запрос отправлен {formatDate(me.setup_help_requested_at)}. Менеджер позвонит на {me.phone} или напишет на {me.email}.
              </Alert>
            </div>
          ) : (
            <>
              {me.setup_help_contacted_at && (
                <p className="mt-2 text-[13px] text-ink-3">Менеджер связался с вами {formatDay(me.setup_help_contacted_at)}. Нужна ещё помощь — нажмите снова.</p>
              )}
              <Button className="mt-3 w-full sm:w-auto" variant="secondary" loading={busy} onClick={ask}>
                Попросить помочь с настройкой
              </Button>
            </>
          )}
          {error && (
            <div className="mt-3">
              <Alert>{error}</Alert>
            </div>
          )}
        </div>
      </div>
    </Card>
  )
}

function HelpCard() {
  const support = usePublicConfig()?.support
  return (
    <Card className="p-6" data-testid="help-card">
      <h2 className="text-[16px] font-bold text-ink">Нужна помощь?</h2>
      <p className="mt-1 text-[14px] text-ink-2">Позвоните или напишите — ответим и подскажем.</p>
      {support ? (
        <SupportLinks support={support} email className="mt-3 text-[14px]" />
      ) : (
        <a href="mailto:info@noxsw.kz" className="mt-3 inline-block text-[14px] font-semibold text-ink-2 hover:text-accent">
          info@noxsw.kz
        </a>
      )}
    </Card>
  )
}

function Faq() {
  const config = usePublicConfig()
  const perDialog = config?.coins_per_dialog ?? 37
  const items: [string, ReactNode][] = [
    [
      'Что такое коины?',
      <>
        Коины — это расход искусственного интеллекта на ответы бота. Один диалог с клиентом в среднем стоит около {perDialog}{' '}
        коинов. Сколько осталось и на сколько диалогов хватит, видно в блоке «Коины». Месячный пакет сгорает в конце месяца,
        докупленные коины — нет.
      </>,
    ],
    [
      'Что будет после теста?',
      <>
        Бот перестанет отвечать клиентам, но настройки, услуги и переписка сохранятся. Выберите тариф и оплатите — бот
        продолжит с того же места, заново ничего настраивать не нужно. За 3 дня и за день до конца теста пришлём письмо.
      </>,
    ],
    [
      'Как оплатить от ТОО или ИП?',
      <>
        Выберите тариф и способ «Оплата по счёту через менеджера». Менеджер пришлёт счёт на вашу компанию, оплатите его
        банковским переводом. Закрывающие документы — по запросу менеджеру.
      </>,
    ],
    [
      'Где настраивать бота?',
      <>
        В панели бота — кнопка «Открыть панель бота» вверху кабинета. Там подключаете WhatsApp, вносите услуги и цены, читаете
        переписку. Здесь, в кабинете, — тариф, оплата и коины.
      </>,
    ],
  ]
  return (
    <Card className="p-6">
      <h2 className="text-[16px] font-bold text-ink">Частые вопросы</h2>
      <div className="mt-3 flex flex-col divide-y divide-line border-y border-line">
        {items.map(([q, a]) => (
          <details key={q} className="group py-1">
            <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 py-2 text-[14.5px] font-semibold text-ink [&::-webkit-details-marker]:hidden">
              {q}
              <ChevronDown className="size-4 flex-none text-ink-3 transition-transform group-open:rotate-180" aria-hidden />
            </summary>
            <p className="pb-3 text-[14px] text-ink-2">{a}</p>
          </details>
        ))}
      </div>
    </Card>
  )
}

function StatusView({ me, setMe }: { me: ClientMe; setMe: (me: ClientMe) => void }) {
  const icon = 'size-9'
  const free = me.tariff === FREE_CODE
  switch (me.status) {
    case 'verified':
      return (
        <StateBlock title="Выберите тариф">
          <p className="mb-5">E-mail подтверждён. Выберите тариф и способ оплаты или начните с бесплатного теста.</p>
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
          {free && <TrialInfo me={me} />}
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
          {me.admin_url && (
            <a
              href={me.admin_url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-2 flex h-12 w-full items-center justify-center gap-2 rounded-nx bg-accent px-5 text-[15.5px] font-bold text-white shadow-[0_1px_2px_rgb(0_0_0/0.12)] hover:bg-accent-hover sm:inline-flex sm:w-auto"
            >
              <LayoutDashboard className="size-5" aria-hidden /> Открыть панель бота
              <ExternalLink className="size-4 opacity-80" aria-hidden />
            </a>
          )}
          <p className="mt-3">Здесь — тариф, оплата и коины. Настройка бота и переписка — в панели бота.</p>
          {me.admin_url && <p className="mt-1 break-all text-[13px] text-ink-3">Адрес панели: {me.admin_url}</p>}
          <SetupLink available={me.setup_link_available} />
          {free && (
            <>
              <TrialInfo me={me} />
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
            <p>
              Бот сейчас не отвечает клиентам. Настройки, услуги и переписка сохранены — выберите тариф, и после оплаты бот
              продолжит с того же места.
            </p>
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
          <Card className="overflow-hidden">
            {(() => {
              const steps = stepsFor(me.status, me.tariff === FREE_CODE)
              return steps ? (
                <div className="border-b border-line bg-sunken/60 px-6 py-4">
                  <Steps {...steps} />
                </div>
              ) : null
            })()}
            <div className="p-6">
              <StatusView me={me} setMe={setMe} />
            </div>
          </Card>
          {!me.local_install && ['approved', 'provisioning', 'active', 'failed'].includes(me.status) && (
            <SetupHelp me={me} onChange={setMe} />
          )}
          {me.llm_enabled && !['removed', 'rejected'].includes(me.status) && <CoinsPanel />}
          <Faq />
          <HelpCard />
          <Card className="p-6">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-[16px] font-bold text-ink">Данные заявки</h2>
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
