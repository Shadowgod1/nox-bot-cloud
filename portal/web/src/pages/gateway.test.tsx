import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ApplicationDetail } from '../api'
import { bodyOf, json, mockFetch, renderAt } from '../test/utils'
import { CoinsPanel } from './CoinsPanel'
import { GatewayPanel, InvoicesPanel } from './staff/GatewayPanel'
import { LocalAccountForm } from './staff/LocalAccount'

const USAGE = {
  balance: {
    coins_left: 27450.6, monthly_limit: 30000, monthly_coins: 30000, topup_coins: 10000, spent: 12549.4,
    period_start: '2026-11-01T00:00:00Z', period_end: '2026-12-01T00:00:00Z', status: 'active',
  },
  by_day: [
    { day: '2026-11-24', coins: 400.5 },
    { day: '2026-11-25', coins: 1200 },
  ],
  by_model: [{ provider: 'anthropic', model: 'claude-sonnet-5-5', requests: 321, input_tokens: 1, output_tokens: 2, coins: 1600.5 }],
}
const INVOICES = [
  { id: 4, number: 'NOX-2026-00004', kind: 'topup', tariff: 'topup', amount_kzt: 9900, coins: 10000, issued_at: '2026-11-20T00:00:00Z', period_start: null, paid: true },
  { id: null, number: 'NOX-2026-00001', kind: 'subscription', tariff: 'start', amount_kzt: 70000, coins: 30000, issued_at: '2026-11-01T00:00:00Z', period_start: null, paid: true },
]

afterEach(() => vi.restoreAllMocks())

describe('CoinsPanel (cabinet)', () => {
  it('shows balance, usage by day and model, invoices', async () => {
    mockFetch({ 'GET /api/me/usage': () => json(USAGE), 'GET /api/me/invoices': () => json(INVOICES) })
    render(
      <MemoryRouter>
        <CoinsPanel />
      </MemoryRouter>,
    )
    expect(await screen.findByTestId('coins-left')).toHaveTextContent('27 450')
    expect(screen.getByText('10 000')).toBeInTheDocument()
    expect(screen.getByText('claude-sonnet-5-5')).toBeInTheDocument()
    expect(screen.getByText('25.11.2026')).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '69')
    expect(await screen.findByText('№ NOX-2026-00004')).toHaveAttribute('href', '/invoice/NOX-2026-00004')
    expect(screen.getByText('№ NOX-2026-00001')).toHaveAttribute('href', '/invoice')
  })

  it('"Пополнить" issues a top-up invoice and opens it', async () => {
    const fetch = mockFetch({
      'GET /api/me/usage': () => json(USAGE),
      'GET /api/me/invoices': () => json([]),
      'POST /api/me/topup': () => json({ number: 'NOX-2026-00009', kind: 'topup' }),
    })
    document.cookie = 'nox_csrf=tok'
    renderAt('/', <CoinsPanel />)
    await screen.findByTestId('coins-left')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Пополнить' }))
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/invoice/NOX-2026-00009'))
    const call = fetch.mock.calls.find(([u]) => String(u) === '/api/me/topup')
    expect(new Headers((call?.[1] as RequestInit).headers).get('X-Nox-CSRF')).toBe('tok')
  })
})

function app(over: Partial<ApplicationDetail> = {}): ApplicationDetail {
  return {
    id: 5, company: 'ТОО Пример', contact_name: 'Айгерим', email: 'a@example.kz', phone: '+7', slug: 'primer',
    status: 'active', coins_limit: 30000, tariff: 'start', created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z',
    bot_host: 'primer.example.kz', admin_url: null, port: null, reject_reason: null, last_error: null, email_verified_at: null,
    register_ip: null, invoice_no: null, invoice_amount: null, invoice_issued_at: null, payments: [], jobs: [], audit: [],
    local_install: false,
    llm: { ...USAGE.balance, status: 'active' as const },
    keys: [{ id: 1, prefix: 'nox_vk_AbCdEf', status: 'active', created_at: '2026-11-01T00:00:00Z', last_used_at: null, revoked_at: null }],
    invoices: [],
    ...over,
  }
}

describe('GatewayPanel (staff)', () => {
  it('cloud reissue queues a rekey job and never shows the key', async () => {
    const url = 'POST /api/staff/applications/5/key/reissue'
    mockFetch({ [url]: () => json({ ok: true, prefix: 'nox_vk_NewKey', key: null, job_id: 9 }) })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const onChanged = vi.fn()
    render(<GatewayPanel app={app()} onChanged={onChanged} />)
    expect(screen.getByText(/nox_vk_AbCdEf/)).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Перевыпустить ключ' }))
    expect(await screen.findByText(/nox_vk_NewKey… передаётся боту/)).toBeInTheDocument()
    expect(screen.queryByTestId('one-time-key')).toBeNull()
    expect(onChanged).toHaveBeenCalled()
  })

  it('local reissue shows the full key once; revoke asks first', async () => {
    const full = 'nox_vk_' + 'Z'.repeat(43)
    const fetch = mockFetch({
      'POST /api/staff/applications/5/key/reissue': () => json({ ok: true, prefix: full.slice(0, 13), key: full, job_id: null }),
      'POST /api/staff/applications/5/key/revoke': () => json({ ok: true }),
    })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const user = userEvent.setup()
    render(<GatewayPanel app={app({ local_install: true })} gatewayUrl="https://bot.example.kz" onChanged={() => undefined} />)
    await user.click(screen.getByRole('button', { name: 'Перевыпустить ключ' }))
    expect(await screen.findByTestId('one-time-key')).toHaveTextContent(full)
    expect(screen.getByText('ILER_LLM_GATEWAY_URL=https://bot.example.kz')).toBeInTheDocument()
    confirm.mockReturnValueOnce(false)
    await user.click(screen.getByRole('button', { name: 'Отозвать' }))
    expect(fetch.mock.calls.some(([u]) => String(u).endsWith('/key/revoke'))).toBe(false)
    await user.click(screen.getByRole('button', { name: 'Отозвать' }))
    await waitFor(() => expect(fetch.mock.calls.some(([u]) => String(u).endsWith('/key/revoke'))).toBe(true))
  })

  it('reissue is disabled for a cloud bot that is not running', () => {
    render(<GatewayPanel app={app({ status: 'suspended' })} onChanged={() => undefined} />)
    expect(screen.getByRole('button', { name: 'Перевыпустить ключ' })).toBeDisabled()
  })
})

describe('InvoicesPanel (staff)', () => {
  it('marks a top-up invoice paid with the amount and reference', async () => {
    const url = 'POST /api/staff/invoices/7/mark-paid'
    const fetch = mockFetch({ [url]: () => json({ ok: true }) })
    const onChanged = vi.fn()
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <InvoicesPanel
          app={app({ invoices: [{ id: 7, number: 'NOX-2026-00007', kind: 'topup', tariff: 'topup', amount_kzt: 9900, coins: 10000, issued_at: null, period_start: null, paid: false }] })}
          onChanged={onChanged}
        />
      </MemoryRouter>,
    )
    await user.click(screen.getByRole('button', { name: 'Отметить оплату' }))
    expect(screen.getByLabelText('Получено, ₸')).toHaveValue('9900')
    await user.type(screen.getByLabelText('Платёжное поручение'), 'п/п 12')
    await user.click(screen.getByRole('button', { name: 'Записать оплату' }))
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
    expect(bodyOf(fetch, url)).toEqual({ amount_kzt: 9900, reference: 'п/п 12' })
  })
})

describe('LocalAccountForm', () => {
  it('creates the account and shows the key once', async () => {
    const full = 'nox_vk_' + 'L'.repeat(43)
    const fetch = mockFetch({ 'POST /api/staff/local-accounts': () => json({ account_id: 3, prefix: full.slice(0, 13), key: full }) })
    const config = {
      tariffs: [{ code: 'start', name: 'Старт', price_kzt: 70000, coins: 30000, description: '' }],
      coin_presets: [], instance_domain: 'example.kz', custom_key_supported: true, gateway_url: 'https://bot.example.kz',
    }
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <LocalAccountForm config={config} />
      </MemoryRouter>,
    )
    const submit = screen.getByRole('button', { name: 'Создать и выпустить ключ' })
    expect(submit).toBeDisabled()
    await user.type(screen.getByLabelText('Компания'), 'ТОО Локал')
    await user.type(screen.getByLabelText('Контактное лицо'), 'Ерлан')
    await user.type(screen.getByLabelText('E-mail клиента'), 'l@example.kz')
    await user.type(screen.getByLabelText('Телефон'), '+77011111111')
    await user.type(screen.getByLabelText('Короткое имя (латиница)'), 'Lokal')
    await user.click(submit)
    expect(await screen.findByTestId('one-time-key')).toHaveTextContent(full)
    expect(bodyOf(fetch, 'POST /api/staff/local-accounts')).toEqual({
      company: 'ТОО Локал', contact_name: 'Ерлан', email: 'l@example.kz', phone: '+77011111111', slug: 'lokal', tariff: 'start',
    })
  })
})
