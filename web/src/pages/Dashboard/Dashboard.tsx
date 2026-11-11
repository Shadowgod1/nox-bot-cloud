import { useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { ArrowRight, CircleCheck, LogOut, Smartphone } from 'lucide-react'
import { api } from '../../api/client'
import type { BotSettings, Dashboard as DashboardData, WhatsAppStatus } from '../../api/types'
import { useLoad } from '../../hooks/useLoad'
import { useEvent } from '../../hooks/useEvents'
import { PageBody } from '../../components/Layout'
import { WhatsAppConnect } from '../../components/WhatsAppConnect'
import { Badge, Button, ErrorState, PageHeader, Panel, Spinner, Switch } from '../../components/ui'
import { toast } from '../../components/toast'
import { formatDateTime, plural, ru } from '../../i18n/ru'

const waTone = { open: 'ok', connecting: 'warn', close: 'danger', degraded: 'danger' } as const

export function Dashboard() {
  const { data, error, loading, reload, setData } = useLoad(() => api.get<DashboardData>('/api/dashboard', { silent: true }))
  const [params, setParams] = useSearchParams()
  const [connecting, setConnecting] = useState(params.get('connect') === '1')
  const [toggling, setToggling] = useState(false)
  const [loggingOut, setLoggingOut] = useState(false)

  useEvent('whatsapp.status', (wa) => setData((d) => (d ? { ...d, whatsapp: wa } : d)))

  async function toggleBot(enabled: boolean) {
    setToggling(true)
    try {
      const bot = await api.get<BotSettings>('/api/settings/bot')
      await api.put('/api/settings/bot', { ...bot, enabled })
      setData((d) => (d ? { ...d, bot_enabled: enabled } : d))
      toast.success(enabled ? 'Бот включён' : 'Бот выключен: сообщения сохраняются, но ответов не будет')
    } catch {
      /* toast */
    } finally {
      setToggling(false)
    }
  }

  async function logoutWa() {
    if (!window.confirm('Отключить номер от ILER? Бот перестанет отвечать, пока вы не подключите WhatsApp снова.')) return
    setLoggingOut(true)
    try {
      await api.post('/api/whatsapp/logout')
      setData((d) => (d ? { ...d, whatsapp: { status: 'close', phone: null, last_error: null } } : d))
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

  return (
    <PageBody>
      <PageHeader title="Обзор" description="Состояние бота и работа за сегодня." />

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <Panel
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
                setData((d) => (d ? { ...d, whatsapp: s } : d))
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
                {wa.last_error && <span className="mt-1 block text-[13px] text-danger">{wa.last_error}</span>}
              </p>
              <Button variant="primary" onClick={() => setConnecting(true)}>
                Подключить WhatsApp
              </Button>
            </div>
          )}
        </Panel>

        <Panel title="Бот">
          <Switch
            checked={data.bot_enabled}
            disabled={toggling}
            onChange={toggleBot}
            label={data.bot_enabled ? 'Бот отвечает клиентам' : 'Бот выключен'}
            description={
              data.bot_enabled
                ? 'Выключите, если хотите отвечать только вручную.'
                : 'Входящие сохраняются в «Диалогах», отвечают только операторы.'
            }
          />
        </Panel>
      </div>

      <section aria-labelledby="today-h" className="mt-5 rounded-[var(--radius-panel)] border border-line bg-surface">
        <h2 id="today-h" className="border-b border-line px-5 py-3.5 text-[15px] font-semibold">
          Сегодня
        </h2>
        <dl className="grid grid-cols-2 divide-line md:grid-cols-4 md:divide-x">
          <Stat label={plural(t.conversations, ['диалог', 'диалога', 'диалогов'])} value={t.conversations} to="/conversations" />
          <Stat label={plural(t.replies, ['ответ бота', 'ответа бота', 'ответов бота'])} value={t.replies} />
          <Stat label={plural(t.bookings, ['заявка', 'заявки', 'заявок'])} value={t.bookings} to="/bookings" />
          <Stat
            label="ждут оператора"
            value={t.needs_operator ?? 0}
            to="/conversations"
            attention={(t.needs_operator ?? 0) > 0}
          />
        </dl>
      </section>

      <Panel title="Ошибки" description="Последние сбои модели, WhatsApp и интеграций." className="mt-5" bodyClassName="p-0">
        {data.errors.length === 0 ? (
          <div className="flex items-center gap-2.5 px-5 py-4 text-sm text-ink-2">
            <CircleCheck className="size-4 text-ok" aria-hidden />
            Ошибок не было.
          </div>
        ) : (
          <ul className="divide-y divide-line">
            {data.errors.map((e, i) => (
              <li key={i} className="grid gap-x-4 gap-y-0.5 px-5 py-3 text-sm sm:grid-cols-[150px_110px_1fr]">
                <span className="tabular text-[13px] text-ink-3">{formatDateTime(e.at)}</span>
                <span className="text-[13px] font-medium text-ink-2">{sourceLabel(e.source)}</span>
                <span className="text-ink">{e.message}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </PageBody>
  )
}

function sourceLabel(s: string) {
  return ({ llm: 'Модель', whatsapp: 'WhatsApp', senet: 'SENET', smtp: 'Почта', tool: 'Инструмент' } as Record<string, string>)[s] ?? s
}

function Stat({ label, value, to, attention }: { label: string; value: number; to?: string; attention?: boolean }) {
  return (
    <div className={`group relative flex flex-col-reverse px-5 py-4 ${to ? 'hover:bg-sunken' : ''}`}>
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
