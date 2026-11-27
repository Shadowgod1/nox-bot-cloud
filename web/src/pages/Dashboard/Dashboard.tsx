import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { ArrowRight, CircleAlert, CircleCheck, FlaskConical, LogOut, Smartphone } from 'lucide-react'
import { api } from '../../api/client'
import type { BotSettings, Dashboard as DashboardData, WhatsAppStatus } from '../../api/types'
import { useBrand } from '../../brand'
import { useLoad } from '../../hooks/useLoad'
import { useEvent } from '../../hooks/useEvents'
import { PageBody } from '../../components/Layout'
import { WhatsAppConnect } from '../../components/WhatsAppConnect'
import { Badge, Button, ErrorState, PageHeader, Panel, Spinner } from '../../components/ui'
import { toast } from '../../components/toast'
import { plural, ru } from '../../i18n/ru'
import { StatusHero } from './StatusHero'
import { CoinsCard } from './CoinsCard'
import { RoiCard } from './RoiCard'
import { ActivityCard } from './ActivityCard'
import { coinsZeroText, deriveStatus } from './health'

const waTone = { open: 'ok', connecting: 'warn', close: 'danger', degraded: 'danger' } as const

/** Silent background refresh so "is the bot alive" and the coin counter stay current. */
const REFRESH_MS = 60_000

export function Dashboard() {
  const brand = useBrand()
  const { data, error, loading, reload, setData } = useLoad(() => api.get<DashboardData>('/api/dashboard', { silent: true }))
  const [params, setParams] = useSearchParams()
  const [connecting, setConnecting] = useState(params.get('connect') === '1')
  const [toggling, setToggling] = useState(false)
  const [loggingOut, setLoggingOut] = useState(false)

  useEvent('whatsapp.status', (wa) => {
    setData((d) => (d ? { ...d, whatsapp: { status: wa.status, phone: wa.phone } } : d))
  })

  useEffect(() => {
    const id = window.setInterval(() => {
      if (document.visibilityState === 'visible') void reload()
    }, REFRESH_MS)
    return () => window.clearInterval(id)
  }, [reload])

  async function toggleBot(enabled: boolean) {
    setToggling(true)
    try {
      // PUT /api/settings/bot replaces the whole object: send the current settings with `enabled` changed.
      const bot = await api.get<BotSettings>('/api/settings/bot')
      const saved = await api.put<BotSettings>('/api/settings/bot', { ...bot, enabled })
      setData((d) => (d ? { ...d, bot_enabled: saved.enabled } : d))
      toast.success(enabled ? 'Бот включён' : 'Бот выключен: сообщения сохраняются, но ответов не будет')
    } catch {
      /* toast */
    } finally {
      setToggling(false)
    }
  }

  function startConnect() {
    setConnecting(true)
    document.getElementById('wa-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  async function logoutWa() {
    if (!window.confirm(`Отключить номер от ${brand.name}? Бот перестанет отвечать, пока вы не подключите WhatsApp снова.`)) return
    setLoggingOut(true)
    try {
      await api.post('/api/whatsapp/logout')
      setData((d) => (d ? { ...d, whatsapp: { status: 'close', phone: null } } : d))
    } catch {
      /* toast */
    } finally {
      setLoggingOut(false)
    }
  }

  if (loading && !data) return <PageBody><Spinner /></PageBody>
  if (error && !data) return <PageBody><ErrorState message={error.message} onRetry={reload} /></PageBody>
  if (!data) return null

  const wa = data.whatsapp
  const t = data.today
  const e24 = data.errors_24h
  const errorsTotal = e24.llm + e24.send
  const status = deriveStatus(data)
  const coinsZero = coinsZeroText(data.coins)

  return (
    <PageBody>
      <PageHeader
        title="Обзор"
        description="Работает ли бот, как идут диалоги и сколько осталось коинов."
        actions={
          <Link
            to="/test-chat"
            className="inline-flex h-9 items-center gap-1.5 rounded-[7px] border border-line-strong bg-surface px-3.5 text-sm font-medium text-ink hover:bg-sunken"
          >
            <FlaskConical className="size-4" aria-hidden />
            Проверить бота
          </Link>
        }
      />

      <div className="space-y-5">
        {coinsZero && (
          <div
            role="alert"
            data-testid="coins-zero-banner"
            className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-[var(--radius-panel)] border border-danger/20 bg-danger-soft px-5 py-3 text-sm text-ink"
          >
            <CircleAlert className="size-4 shrink-0 text-danger" aria-hidden />
            <span className="font-medium">{coinsZero}</span>
            {data.coins.blocked === 'key_revoked' ? (
              <span className="text-ink-2">Обратитесь в {brand.company}.</span>
            ) : data.coins.topup_url ? (
              <a href={data.coins.topup_url} target="_blank" rel="noopener noreferrer" className="font-medium text-danger underline">
                Пополнить
              </a>
            ) : null}
          </div>
        )}
        <StatusHero
          status={status}
          health={data.health}
          action={
            wa.status !== 'open' && !connecting ? (
              <Button variant="primary" onClick={startConnect}>
                Подключить WhatsApp
              </Button>
            ) : !data.bot_enabled ? (
              <Button variant="primary" loading={toggling} onClick={() => void toggleBot(true)}>
                Включить бота
              </Button>
            ) : undefined
          }
        />
        <ActivityCard />
        {/* wide screens: the two activity strips side by side */}
        <div className="grid gap-5 2xl:grid-cols-2">
          <RoiCard />
          <section aria-labelledby="today-h" className="rounded-[var(--radius-panel)] border border-line bg-surface">
            <h2 id="today-h" className="flex min-h-[57px] items-center border-b border-line px-5 py-3 text-[15px] font-semibold">
              Сегодня
            </h2>
            <dl className="grid grid-cols-2 divide-line md:grid-cols-5 md:divide-x">
              <Stat label={plural(t.conversations, ['диалог', 'диалога', 'диалогов'])} value={t.conversations} to="/conversations" />
              <Stat label={plural(t.client_messages, ['сообщение клиентов', 'сообщения клиентов', 'сообщений клиентов'])} value={t.client_messages} />
              <Stat label={plural(t.bot_replies, ['ответ бота', 'ответа бота', 'ответов бота'])} value={t.bot_replies} />
              <Stat label={`${plural(t.bookings_total, ['заявка', 'заявки', 'заявок'])} за день`} value={t.bookings_total} to="/bookings" />
              <Stat
                label="заявок ждут решения"
                value={t.bookings_pending}
                to="/bookings"
                attention={t.bookings_pending > 0}
              />
            </dl>
          </section>
        </div>

        {/* xl+: coins on the left, the system column (WhatsApp, bot, errors) on the right */}
        <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.75fr)_minmax(360px,1fr)]">
          <CoinsCard coins={data.coins} />
          <div className="space-y-5">
            <h2 className="mt-3 text-[13px] font-semibold uppercase tracking-[0.04em] text-ink-3 xl:sr-only">Система</h2>
            <div className="grid gap-5">
              <Panel
                id="wa-panel"
                title="WhatsApp"
                actions={<Badge tone={waTone[wa.status]} dot>{ru.waStatus[wa.status]}</Badge>}
              >
                {wa.status === 'open' && !connecting ? (
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                      <Smartphone className="size-5 text-ink-3" aria-hidden />
                      <div>
                        <p className="tabular text-sm font-medium text-ink">{wa.phone ?? 'Номер не определён'}</p>
                        <p className="text-[13px] text-ink-2">Бот получает и отправляет сообщения.</p>
                      </div>
                    </div>
                    <Button size="sm" variant="ghost" loading={loggingOut} onClick={logoutWa} icon={<LogOut className="size-3.5" aria-hidden />}>
                      Отключить номер
                    </Button>
                  </div>
                ) : connecting ? (
                  <WhatsAppConnect
                    onConnected={(s: WhatsAppStatus) => {
                      setData((d) => (d ? { ...d, whatsapp: { status: s.status, phone: s.phone } } : d))
                      setConnecting(false)
                      if (params.has('connect')) setParams({}, { replace: true })
                    }}
                  />
                ) : (
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <p className="max-w-[48ch] text-sm text-ink-2">
                      {wa.status === 'degraded'
                        ? 'Сессия открыта, но отправка сообщений не проходит. Переподключите номер.'
                        : 'Номер не подключён. Клиенты пишут, но бот их не видит.'}
                    </p>
                    <Button variant="primary" onClick={startConnect}>
                      Подключить WhatsApp
                    </Button>
                  </div>
                )}
              </Panel>

            </div>

            <Panel
              title="Ошибки за 24 часа"
              description="Сбои обращений к модели и неотправленные сообщения."
              bodyClassName="p-0"
            >
              {errorsTotal === 0 ? (
                <div className="flex items-center gap-2.5 px-5 py-4 text-sm text-ink-2">
                  <CircleCheck className="size-4 text-ok" aria-hidden />
                  Ошибок не было.
                </div>
              ) : (
                <dl className="grid grid-cols-2 divide-x divide-line">
                  <Stat label={plural(e24.llm, ['сбой модели', 'сбоя модели', 'сбоев модели'])} value={e24.llm} attention={e24.llm > 0} />
                  <Stat
                    label={plural(e24.send, ['неотправленное сообщение', 'неотправленных сообщения', 'неотправленных сообщений'])}
                    value={e24.send}
                    attention={e24.send > 0}
                  />
                </dl>
              )}
            </Panel>
          </div>
        </div>
      </div>
    </PageBody>
  )
}

function Stat({ label, value, to, attention }: { label: string; value: number; to?: string; attention?: boolean }) {
  return (
    <div className={`group relative flex flex-col-reverse justify-end px-5 py-4 ${to ? 'hover:bg-sunken' : ''}`}>
      <dt className="mt-1.5 flex items-center gap-1 text-[13px] text-ink-2">
        {label}
        {to && <ArrowRight className="size-3 opacity-0 transition-opacity group-hover:opacity-100" aria-hidden />}
      </dt>
      <dd className={`tabular text-[26px] font-semibold leading-none ${attention ? 'text-danger' : 'text-ink'}`}>
        {value}
        {to && (
          <Link to={to} className="absolute inset-0" aria-label={`${value} ${label}: открыть`}>
            <span className="sr-only">Открыть</span>
          </Link>
        )}
      </dd>
    </div>
  )
}
