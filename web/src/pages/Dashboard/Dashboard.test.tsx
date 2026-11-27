import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { Dashboard } from './Dashboard'
import { formatAgo, formatCoins, formatDayMonth, formatPercent, formatSeconds } from './health'
import { FakeEventSource, mockApi } from '../../test/utils'
import { __resetEventsForTests } from '../../hooks/useEvents'
import type { Dashboard as DashboardData, DashboardCoins } from '../../api/types'

vi.mock('../../hooks/useAuth', () => ({ useAuth: () => ({ isOwner: true }) }))

const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString()

function coins(over: Partial<DashboardCoins> = {}): DashboardCoins {
  return {
    limit: 30000,
    spent: 12400.5,
    remaining: 17599.5,
    percent: 41.3,
    exhausted: false,
    avg_per_day: 1127.3,
    projected_month: 25000,
    runs_out_on: null,
    days_in_month: 30,
    days: [
      { date: '2026-11-01', coins: 980.2 },
      { date: '2026-11-02', coins: 0 },
      { date: '2026-11-03', coins: 1420.3 },
    ],
    by_model: [
      { model: 'claude-sonnet-5-5', coins: 9000 },
      { model: 'claude-haiku-5-5', coins: 3400.5 },
    ],
    unpriced_models: [],
    ...over,
  }
}

function dashboard(over: Partial<DashboardData> = {}): DashboardData {
  return {
    whatsapp: { status: 'open', phone: '77015551234' },
    bot_enabled: true,
    today: { conversations: 14, client_messages: 41, bot_replies: 63, bookings_pending: 0, bookings_total: 3 },
    errors_24h: { llm: 0, send: 0 },
    backup: { status: 'ok', at: minutesAgo(600), message: null },
    health: {
      last_bot_reply_at: minutesAgo(5),
      last_client_message_at: minutesAgo(6),
      avg_reply_ms_today: 2300,
      handovers_today: 1,
      answered_share_today: 0.93,
    },
    coins: coins(),
    ...over,
  }
}

async function renderWith(d: DashboardData) {
  mockApi({ 'GET /api/dashboard': d })
  render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>,
  )
  return screen.findByRole('region', { name: /^Бот / })
}

const forecast = () => screen.getByTestId('coins-forecast')

describe('Dashboard', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    __resetEventsForTests()
    vi.stubGlobal('EventSource', FakeEventSource)
  })

  it('shows green status with health metrics when everything is fine', async () => {
    const hero = await renderWith(dashboard())
    expect(hero).toHaveAttribute('data-level', 'ok')
    expect(within(hero).getByRole('heading', { name: 'Бот работает и отвечает клиентам' })).toBeInTheDocument()
    expect(within(hero).getByText('Последний ответ бота')).toBeInTheDocument()
    expect(within(hero).getByText('5 минут назад')).toBeInTheDocument()
    expect(within(hero).getByText('~2,3 с')).toBeInTheDocument()
    expect(within(hero).getByText('93% диалогов')).toBeInTheDocument()
    expect(within(hero).getByText('1 диалог')).toBeInTheDocument()
  })

  it('forecasts that coins last the month with the projected leftover', async () => {
    await renderWith(dashboard())
    expect(forecast()).toHaveAttribute('data-level', 'ok')
    expect(forecast()).toHaveTextContent(/При текущем расходе \(~1\s127 в день\) коинов хватит до конца месяца, останется ~5\s000/)
    expect(screen.getByTestId('coins-remaining')).toHaveTextContent(/осталось\s*17\s600\s*из 30\s000/)
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '41')
    expect(screen.getByText('Claude Sonnet 5.5')).toBeInTheDocument()
    expect(screen.getByText(/1 коин ≈ \$0\.001 расходов на модель/)).toBeInTheDocument()
  })

  it('clamps the leftover at zero', async () => {
    await renderWith(dashboard({ coins: coins({ projected_month: 31000 }) }))
    expect(forecast()).toHaveTextContent('останется ~0')
  })

  it('goes amber and names the date when the limit runs out before the month end', async () => {
    const hero = await renderWith(dashboard({ coins: coins({ runs_out_on: '2026-11-27', projected_month: 33819 }) }))
    expect(hero).toHaveAttribute('data-level', 'warn')
    expect(within(hero).getByRole('heading', { name: 'Бот работает, но требует внимания' })).toBeInTheDocument()
    expect(within(hero).getByText('При текущем расходе коинов хватит только до 27 ноября')).toBeInTheDocument()
    expect(forecast()).toHaveTextContent(/При текущем расходе \(~1\s127 в день\) коинов хватит до 27 ноября$/)
    expect(forecast()).not.toHaveAttribute('data-level', 'ok')
  })

  it('shows no price warning when every model has a price', async () => {
    await renderWith(dashboard())
    expect(screen.queryByTestId('coins-unpriced')).not.toBeInTheDocument()
  })

  it('names the unpriced models', async () => {
    await renderWith(dashboard({ coins: coins({ unpriced_models: ['gpt-x-new'] }) }))
    expect(screen.getByTestId('coins-unpriced')).toHaveTextContent(
      'Для модели gpt-x-new нет цены — расход посчитан по максимальному тарифу, уточните у ILER.',
    )
  })

  it('goes amber on errors in the last 24 hours', async () => {
    const hero = await renderWith(dashboard({ errors_24h: { llm: 2, send: 0 } }))
    expect(hero).toHaveAttribute('data-level', 'warn')
    expect(within(hero).getByText('2 ошибки за последние 24 часа')).toBeInTheDocument()
  })

  it('goes amber at 80% of the limit', async () => {
    const hero = await renderWith(dashboard({ coins: coins({ spent: 25500, remaining: 4500, percent: 85 }) }))
    expect(hero).toHaveAttribute('data-level', 'warn')
    expect(within(hero).getByText('Израсходовано 85% месячного лимита коинов')).toBeInTheDocument()
  })

  it('goes red when WhatsApp is not connected and offers to connect', async () => {
    const hero = await renderWith(dashboard({ whatsapp: { status: 'close', phone: null }, errors_24h: { llm: 3, send: 1 } }))
    expect(hero).toHaveAttribute('data-level', 'danger')
    expect(within(hero).getByRole('heading', { name: 'Бот не отвечает' })).toBeInTheDocument()
    expect(within(hero).getByText(/WhatsApp не подключён/)).toBeInTheDocument()
    expect(within(hero).getByRole('button', { name: 'Подключить WhatsApp' })).toBeInTheDocument()
  })

  it('goes red when the bot is disabled', async () => {
    const hero = await renderWith(dashboard({ bot_enabled: false }))
    expect(hero).toHaveAttribute('data-level', 'danger')
    expect(within(hero).getByText('Бот выключен — отвечают только операторы')).toBeInTheDocument()
    expect(within(hero).getByRole('button', { name: 'Включить бота' })).toBeInTheDocument()
  })

  it('shows the exhausted state in red', async () => {
    const hero = await renderWith(
      dashboard({ coins: coins({ spent: 30210, remaining: 0, percent: 100.7, exhausted: true, runs_out_on: '2026-11-10' }) }),
    )
    expect(hero).toHaveAttribute('data-level', 'danger')
    expect(within(hero).getByText('Лимит коинов исчерпан — бот передаёт сообщения оператору')).toBeInTheDocument()
    expect(forecast()).toHaveAttribute('data-level', 'danger')
    expect(forecast()).toHaveTextContent('Лимит на этот месяц исчерпан')
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100')
    expect(screen.getByTestId('coins-remaining')).toHaveTextContent(/осталось\s*0\s*из 30\s000/)
  })

  it('shows spend only and "Лимит не задан" when no limit is configured', async () => {
    const hero = await renderWith(
      dashboard({ coins: coins({ limit: 0, remaining: null, percent: null, runs_out_on: null, projected_month: 40000 }) }),
    )
    expect(hero).toHaveAttribute('data-level', 'ok')
    expect(screen.getByText('Лимит не задан')).toBeInTheDocument()
    expect(screen.getByTestId('coins-spent')).toHaveTextContent(/израсходовано\s*12\s401/)
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(screen.queryByTestId('coins-forecast')).not.toBeInTheDocument()
    expect(screen.queryByTestId('coins-remaining')).not.toBeInTheDocument()
  })

  it('a Free test: the exact spend, and the forecast stops at the test end, not the period end', async () => {
    await renderWith(
      dashboard({
        coins: coins({
          source: 'gateway',
          limit: 2000,
          spent: 2.32,
          remaining: 1997,
          percent: 0.1,
          avg_per_day: 2.32,
          projected_month: 34.8,
          period_end: '2026-12-27T00:00:00+05:00',
          trial_end: '2026-12-11T00:00:00+05:00',
          stale: false,
        }),
      }),
    )
    expect(screen.getByText('Тестовый баланс, тест до 11 декабря.')).toBeInTheDocument()
    expect(screen.queryByText(/27 декабря/)).not.toBeInTheDocument()
    expect(screen.getByText(/израсходовано 2,32 коина/)).toBeInTheDocument()
    expect(forecast()).toHaveTextContent(
      /^Тест закончится 11 декабря\. При текущем расходе \(~2,32 в день\) коинов хватит до конца теста, останется ~1\s965$/,
    )
  })

  it('shows the gateway balance with the period end and a top-up link', async () => {
    await renderWith(
      dashboard({
        coins: coins({
          source: 'gateway',
          limit: 10000,
          spent: 6000,
          remaining: 4000,
          percent: 60,
          projected_month: 9000,
          period_end: '2026-12-01T00:00:00+05:00',
          stale: false,
          topup_url: 'https://pay.example.kz/topup',
        }),
      }),
    )
    expect(screen.getByText('Баланс на период до 1 декабря.')).toBeInTheDocument()
    expect(screen.getByTestId('coins-remaining')).toHaveTextContent(/осталось\s*4\s000\s*из 10\s000/)
    const topup = screen.getByTestId('coins-topup')
    expect(topup).toHaveAttribute('href', 'https://pay.example.kz/topup')
    expect(topup).toHaveAttribute('target', '_blank')
    expect(topup).toHaveTextContent('Пополнить')
    expect(forecast()).toHaveTextContent('хватит до 1 декабря')
    expect(screen.queryByTestId('coins-zero-banner')).not.toBeInTheDocument()
    expect(screen.queryByTestId('coins-stale')).not.toBeInTheDocument()
  })

  it('marks a stale gateway balance and has no top-up link without a URL', async () => {
    await renderWith(
      dashboard({
        coins: coins({ source: 'gateway', limit: 10000, spent: 6000, remaining: 4000, percent: 60, stale: true, balance_at: '2026-11-26T09:05:00Z', topup_url: null }),
      }),
    )
    expect(screen.getByTestId('coins-stale')).toHaveTextContent('Не удалось обновить баланс')
    expect(screen.queryByTestId('coins-topup')).not.toBeInTheDocument()
  })

  it('shows the zero-balance banner when gateway coins run out', async () => {
    const hero = await renderWith(
      dashboard({
        coins: coins({
          source: 'gateway',
          limit: 10000,
          spent: 10000,
          remaining: 0,
          percent: 100,
          exhausted: true,
          blocked: 'insufficient_coins',
          topup_url: 'https://pay.example.kz/topup',
        }),
      }),
    )
    const banner = screen.getByTestId('coins-zero-banner')
    expect(banner).toHaveAttribute('role', 'alert')
    expect(banner).toHaveTextContent('Коины закончились — бот передаёт диалоги оператору')
    expect(within(banner).getByRole('link', { name: 'Пополнить' })).toHaveAttribute('href', 'https://pay.example.kz/topup')
    expect(hero).toHaveAttribute('data-level', 'danger')
    expect(forecast()).toHaveTextContent('Коины закончились')
  })

  it('tells the owner to contact the company when model access is suspended', async () => {
    await renderWith(
      dashboard({ coins: coins({ source: 'gateway', exhausted: true, blocked: 'key_revoked', topup_url: null }) }),
    )
    const banner = screen.getByTestId('coins-zero-banner')
    expect(banner).toHaveTextContent('Доступ к моделям приостановлен — бот передаёт диалоги оператору')
    expect(banner).toHaveTextContent(/Обратитесь в (ILER|Nox service)\./)
  })

  it('shows no zero-balance banner while coins are left', async () => {
    await renderWith(dashboard())
    expect(screen.queryByTestId('coins-zero-banner')).not.toBeInTheDocument()
    expect(screen.queryByTestId('coins-topup')).not.toBeInTheDocument()
  })
})

describe('dashboard formatting', () => {
  it('formats relative time in Russian', () => {
    const now = Date.parse('2026-11-12T10:00:00Z')
    expect(formatAgo('2026-11-12T09:59:40Z', now)).toBe('только что')
    expect(formatAgo('2026-11-12T09:59:00Z', now)).toBe('1 минуту назад')
    expect(formatAgo('2026-11-12T09:58:00Z', now)).toBe('2 минуты назад')
    expect(formatAgo('2026-11-12T09:49:00Z', now)).toBe('11 минут назад')
    expect(formatAgo('2026-11-12T07:00:00Z', now)).toBe('3 часа назад')
    expect(formatAgo('2026-11-10T09:00:00Z', now)).toBe('2 дня назад')
  })

  it('never shows 100% before the limit is used up', () => {
    expect(formatPercent(99.7)).toBe('99%')
    expect(formatPercent(99)).toBe('99%')
    expect(formatPercent(100)).toBe('100%')
    expect(formatPercent(98.6)).toBe('99%')
    expect(formatPercent(41.3)).toBe('41%')
    expect(formatPercent(0.5)).toBe('0,5%')
  })

  it('rounds spent coins by one rule: two decimals under 100, whole coins from 100', () => {
    expect(formatCoins(2.318)).toBe('2,32')
    expect(formatCoins(0.4)).toBe('0,4')
    expect(formatCoins(56.789)).toBe('56,79')
    expect(formatCoins(1234.6)).toMatch(/^1\s235$/)
    expect(formatCoins(3)).toBe('3')
  })

  it('formats dates and seconds', () => {
    expect(formatDayMonth('2026-11-27')).toBe('27 ноября')
    expect(formatSeconds(2300)).toBe('~2,3 с')
    expect(formatSeconds(800)).toBe('~0,8 с')
  })
})
