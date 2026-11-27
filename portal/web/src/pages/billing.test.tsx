import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { browser, resetPublicConfig } from '../api'
import { bodyOf, json, mockFetch, renderAt } from '../test/utils'
import { Cabinet } from './Cabinet'
import { MockCheckout } from './MockCheckout'
import { ApproveForm } from './staff/ApproveForm'
import { IssueInvoiceForm } from './staff/IssueInvoiceForm'
import { TariffPicker } from './TariffPicker'

const TARIFFS = [
  { code: 'start', name: 'Старт', price_kzt: 15000, coins: 30000, description: 'a' },
  { code: 'business', name: 'Бизнес', price_kzt: 39000, coins: 100000, description: 'b' },
]
const FREE = { code: 'free', name: 'Free (тест)', price_kzt: 0, coins: 2000, description: 'Пробный тариф для теста бота', available: true }
const CONFIG_OFF = { instance_domain: 'example.kz', online_payments: false, online_methods: [], free_days: 14 }
const CONFIG_ON = { ...CONFIG_OFF, online_payments: true, online_methods: ['card', 'kaspi_qr'] }

beforeEach(() => {
  resetPublicConfig()
  vi.restoreAllMocks()
})

describe('TariffPicker', () => {
  it('Free first and highlighted, then the paid tariffs; invoice through a manager when online is off', async () => {
    const fetch = mockFetch({
      'GET /api/tariffs': () => json([...TARIFFS, FREE]),
      'GET /api/public-config': () => json(CONFIG_OFF),
      'POST /api/me/tariff': () => json({ status: 'awaiting_invoice' }),
    })
    const onChosen = vi.fn()
    const user = userEvent.setup()
    render(<TariffPicker onChosen={onChosen} />)
    const radios = await screen.findAllByRole('radio')
    expect(radios).toHaveLength(3)
    expect(radios[0]).toHaveTextContent('Free')
    expect(radios[1]).toHaveTextContent('Старт')
    expect(radios[2]).toHaveTextContent('Бизнес')
    const free = screen.getByRole('radio', { name: /Free \(тест\)/ })
    expect(free.className).toContain('border-accent')
    expect(free).toHaveTextContent('Начните с этого')
    expect(free).toHaveTextContent('0 ₸14 дней теста')
    expect(free).toHaveTextContent('Бот подключается сразу, без оплаты и без карты')
    const business = screen.getByRole('radio', { name: /Бизнес/ })
    expect(business).toHaveTextContent('39 000 ₸')
    await user.click(business)
    expect(screen.queryByRole('radio', { name: /Оплатить онлайн/ })).toBeNull()
    expect(screen.getByRole('radio', { name: /Оплата по счёту через менеджера/ })).toHaveAttribute('aria-checked', 'true')
    await user.click(screen.getByRole('button', { name: 'Запросить счёт у менеджера' }))
    await waitFor(() => expect(onChosen).toHaveBeenCalled())
    expect(bodyOf(fetch, 'POST /api/me/tariff')).toEqual({ tariff: 'business', method: 'invoice' })
  })

  it('online payment goes to the checkout page', async () => {
    const fetch = mockFetch({
      'GET /api/tariffs': () => json([...TARIFFS, FREE]),
      'GET /api/public-config': () => json(CONFIG_ON),
      'POST /api/me/tariff': () => json({ status: 'payment_pending', payment_url: 'https://bot.example.kz/pay/mock/m1' }),
    })
    const go = vi.spyOn(browser, 'go').mockImplementation(() => undefined)
    const onChosen = vi.fn()
    const user = userEvent.setup()
    render(<TariffPicker onChosen={onChosen} />)
    await user.click(await screen.findByRole('radio', { name: /Старт/ }))
    const onlineCard = await screen.findByRole('radio', { name: /Оплатить онлайн/ })
    expect(onlineCard).toHaveTextContent('Kaspi QR')
    await user.click(onlineCard)
    await user.click(screen.getByRole('button', { name: 'Перейти к оплате' }))
    await waitFor(() => expect(go).toHaveBeenCalledWith('https://bot.example.kz/pay/mock/m1'))
    expect(bodyOf(fetch, 'POST /api/me/tariff')).toEqual({ tariff: 'start', method: 'online' })
    expect(onChosen).not.toHaveBeenCalled()
  })

  it('Free connects at once without a payment method', async () => {
    const fetch = mockFetch({
      'GET /api/tariffs': () => json([...TARIFFS, FREE]),
      'GET /api/public-config': () => json(CONFIG_ON),
      'POST /api/me/tariff': () => json({ status: 'approved', tariff: 'free' }),
    })
    const onChosen = vi.fn()
    const user = userEvent.setup()
    render(<TariffPicker onChosen={onChosen} />)
    await user.click(await screen.findByRole('radio', { name: /Free/ }))
    expect(screen.queryByText('Способ оплаты')).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Подключить бесплатный тест' }))
    await waitFor(() => expect(onChosen).toHaveBeenCalled())
    expect(bodyOf(fetch, 'POST /api/me/tariff')).toEqual({ tariff: 'free' })
  })

  it('Free without places is disabled; upgrade mode hides Free', async () => {
    mockFetch({
      'GET /api/tariffs': () => json([...TARIFFS, { ...FREE, available: false }]),
      'GET /api/public-config': () => json(CONFIG_OFF),
    })
    const { unmount } = render(<TariffPicker onChosen={() => undefined} />)
    const free = await screen.findByRole('radio', { name: /Free/ })
    expect(free).toBeDisabled()
    expect(free).toHaveTextContent('Тестовые места закончились, выберите тариф или напишите менеджеру')
    unmount()
    render(<TariffPicker upgrade current="free" onChosen={() => undefined} />)
    await screen.findByRole('radio', { name: /Старт/ })
    expect(screen.queryByRole('radio', { name: /Free/ })).toBeNull()
  })
})

describe('Cabinet states', () => {
  const ME = {
    kind: 'client', email: 'o@example.kz', company: 'ТОО', contact_name: 'А', phone: '+7', slug: 'primer',
    bot_host: 'primer.example.kz', admin_url: null, reject_reason: null, setup_link_available: false, tariff: 'business',
    invoice_no: null, invoice_amount: null, created_at: '2026-11-20T00:00:00Z',
  }

  it('awaiting_invoice: the manager will issue the invoice', async () => {
    mockFetch({
      'GET /api/me': () => json({ ...ME, status: 'awaiting_invoice', pay_method: 'invoice' }),
      'GET /api/tariffs': () => json(TARIFFS),
    })
    renderAt('/cabinet', <Cabinet />)
    expect(await screen.findByText('Менеджер свяжется с вами и выставит счёт')).toBeInTheDocument()
    expect(await screen.findByText(/тариф «Бизнес» и оплату по счёту/)).toBeInTheDocument()
    expect(screen.getAllByText('Ждёт счёта').length).toBeGreaterThan(0)
  })

  it('awaiting_payment offers online payment of the issued invoice', async () => {
    const fetch = mockFetch({
      'GET /api/me': () => json({ ...ME, status: 'awaiting_payment', invoice_no: 'NOX-2026-00003', invoice_amount: 39000 }),
      'GET /api/public-config': () => json(CONFIG_ON),
      'POST /api/me/invoices/NOX-2026-00003/pay-online': () => json({ redirect_url: '/pay/mock/m2', qr_payload: null }),
    })
    const go = vi.spyOn(browser, 'go').mockImplementation(() => undefined)
    renderAt('/cabinet', <Cabinet />)
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Оплатить онлайн' }))
    await waitFor(() => expect(go).toHaveBeenCalledWith('/pay/mock/m2'))
    expect(fetch).toHaveBeenCalled()
  })

  it('Free trial ended: upgrade with the same bot', async () => {
    mockFetch({
      'GET /api/me': () => json({ ...ME, status: 'suspended', tariff: 'free', suspend_reason: 'trial_ended', upgrade_tariff: null }),
      'GET /api/me/invoices': () => json([]),
      'GET /api/tariffs': () => json([...TARIFFS, FREE]),
      'GET /api/public-config': () => json(CONFIG_OFF),
    })
    renderAt('/cabinet', <Cabinet />)
    expect(await screen.findByText('Тестовый период закончился')).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Выбрать тариф' }))
    await screen.findByRole('radio', { name: /Старт/ })
    expect(screen.queryByRole('radio', { name: /Free/ })).toBeNull()
  })
})

describe('Kaspi QR mode', () => {
  const KASPI = { ...CONFIG_OFF, online_payments: true, online_methods: ['kaspi_qr'], online_mode: 'qr' }
  const LINK = 'https://kaspi.example/pay/nox?account=202600005&amount=15000'

  it('online choice stays in the cabinet (no redirect) and the cabinet shows the QR', async () => {
    const go = vi.spyOn(browser, 'go').mockImplementation(() => undefined)
    mockFetch({
      'GET /api/tariffs': () => json(TARIFFS),
      'GET /api/public-config': () => json(KASPI),
      'POST /api/me/tariff': () => json({ status: 'payment_pending', payment_url: LINK }),
    })
    const onChosen = vi.fn()
    const user = userEvent.setup()
    render(<TariffPicker onChosen={onChosen} />)
    await user.click(await screen.findByRole('radio', { name: /Старт/ }))
    const card = await screen.findByRole('radio', { name: /Оплатить онлайн/ })
    expect(card).toHaveTextContent('Kaspi QR')
    await user.click(card)
    await user.click(screen.getByRole('button', { name: 'Перейти к оплате' }))
    await waitFor(() => expect(onChosen).toHaveBeenCalled())
    expect(go).not.toHaveBeenCalled()
  })

  it('payment_pending: QR, «Открыть в Kaspi» and the invoice for a bank transfer', async () => {
    mockFetch({
      'GET /api/me': () =>
        json({
          kind: 'client', email: 'o@example.kz', company: 'ТОО', contact_name: 'А', phone: '+7', slug: 'primer',
          bot_host: 'primer.example.kz', admin_url: null, reject_reason: null, setup_link_available: false, tariff: 'start',
          created_at: '2026-11-20T00:00:00Z', status: 'payment_pending', invoice_no: 'NOX-2026-00005', invoice_amount: 15000,
          payment_url: LINK,
        }),
      'GET /api/tariffs': () => json(TARIFFS),
      'GET /api/public-config': () => json(KASPI),
    })
    renderAt('/cabinet', <Cabinet />)
    expect(await screen.findByRole('img', { name: 'QR-код для оплаты в Kaspi' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Открыть в Kaspi/ })).toHaveAttribute('href', LINK)
    expect(screen.getByText('К оплате: 15 000 ₸')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /для банковского перевода/ })).toHaveAttribute('href', '/invoice')
    expect(screen.queryByRole('link', { name: 'Перейти к оплате' })).toBeNull()
  })
})

describe('Mock checkout', () => {
  it('«Оплачено» posts the signed webhook and returns to the cabinet', async () => {
    const fetch = mockFetch({
      'GET /api/payments/mock/m1': () =>
        json({ payment_id: 'm1', purpose_ru: 'Подключение', amount_kzt: 70000, status: 'pending', invoice_no: null, return_url: '/cabinet' }),
      'POST /api/payments/mock/m1/sign': () =>
        json({ webhook_url: '/api/payments/webhook/mock', payload: '{"payment_id":"m1"}', signature_header: 'X-Nox-Mock-Signature', signature: 'abc' }),
      'POST /api/payments/webhook/mock': () => json({ ok: true, outcome: 'applied' }),
    })
    document.cookie = 'nox_csrf=tok'
    const { MemoryRouter: Router, Route, Routes } = await import('react-router')
    render(
      <Router initialEntries={['/pay/mock/m1']}>
        <Routes>
          <Route path="/pay/mock/:id" element={<MockCheckout />} />
          <Route path="/cabinet" element={<div data-testid="where">cabinet</div>} />
        </Routes>
      </Router>,
    )
    expect(await screen.findByTestId('mock-amount')).toHaveTextContent('70 000 ₸')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Оплачено' }))
    expect(await screen.findByTestId('where')).toHaveTextContent('cabinet')
    expect(bodyOf(fetch, 'POST /api/payments/mock/m1/sign')).toEqual({ result: 'succeeded' })
    const hook = fetch.mock.calls.find(([u]) => String(u) === '/api/payments/webhook/mock')
    expect((hook?.[1] as RequestInit).body).toBe('{"payment_id":"m1"}')
    expect(new Headers((hook?.[1] as RequestInit).headers).get('X-Nox-Mock-Signature')).toBe('abc')
  })
})

describe('Staff: issue invoice', () => {
  const URL_ = 'POST /api/staff/applications/5/issue-invoice'

  it('subscription: tariff price prefilled, editable amount and comment', async () => {
    const fetch = mockFetch({ [URL_]: () => json({ ok: true, number: 'NOX-2026-00011', amount_kzt: 35000 }) })
    const onDone = vi.fn()
    const user = userEvent.setup()
    render(<IssueInvoiceForm applicationId={5} kind="subscription" tariffs={TARIFFS} defaultTariff="business" submitLabel="Выставить счёт" onDone={onDone} />)
    const amount = screen.getByLabelText('Сумма счёта, ₸')
    expect(amount).toHaveValue('39000')
    await user.selectOptions(screen.getByLabelText('Тариф'), 'start')
    expect(amount).toHaveValue('15000')
    await user.clear(amount)
    await user.type(amount, '35000')
    await user.type(screen.getByLabelText('Комментарий (необязательно, печатается в счёте)'), 'по договору')
    await user.click(screen.getByRole('button', { name: 'Выставить счёт' }))
    await waitFor(() => expect(onDone).toHaveBeenCalledWith('NOX-2026-00011'))
    expect(bodyOf(fetch, URL_)).toEqual({ kind: 'subscription', amount_kzt: 35000, tariff: 'start', comment: 'по договору' })
  })

  it('top-up: amount and coins of the pack; zero is blocked', async () => {
    const fetch = mockFetch({ [URL_]: () => json({ ok: true, number: 'NOX-2026-00012', amount_kzt: 9900 }) })
    const user = userEvent.setup()
    render(<IssueInvoiceForm applicationId={5} kind="topup" tariffs={[]} defaultAmount={9900} defaultCoins={10000} submitLabel="Выставить счёт на пополнение" onDone={() => undefined} />)
    expect(screen.queryByLabelText('Тариф')).toBeNull()
    const coinsInput = screen.getByLabelText('Коинов в пакете')
    await user.clear(coinsInput)
    await user.type(coinsInput, '0')
    expect(screen.getByRole('button', { name: 'Выставить счёт на пополнение' })).toBeDisabled()
    await user.clear(coinsInput)
    await user.type(coinsInput, '20000')
    await user.click(screen.getByRole('button', { name: 'Выставить счёт на пополнение' }))
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(bodyOf(fetch, URL_)).toEqual({ kind: 'topup', amount_kzt: 9900, coins: 20000 })
  })
})

describe('ApproveForm in payment mode', () => {
  const config = { tariffs: TARIFFS, coin_presets: [30000, 100000, 300000], instance_domain: 'example.kz', custom_key_supported: true }
  const URL_ = 'POST /api/staff/applications/3/mark-paid'

  it('prefills the invoice amount and tariff coins, then marks paid', async () => {
    const fetch = mockFetch({ [URL_]: () => json({ ok: true }) })
    const onDone = vi.fn()
    const user = userEvent.setup()
    render(
      <ApproveForm mode="payment" applicationId={3} defaultSlug="primer" config={config} invoiceNo="NOX-2026-00001"
        invoiceAmount={39000} tariffCoins={100000} onDone={onDone} />,
    )
    expect(screen.getByLabelText('Получено, ₸')).toHaveValue('39000')
    await user.type(screen.getByLabelText('Платёжное поручение'), 'п/п 77')
    await user.click(screen.getByRole('button', { name: 'Отметить оплату и создать бота' }))
    await waitFor(() => expect(onDone).toHaveBeenCalled())
    expect(bodyOf(fetch, URL_)).toEqual({ coins: 100000, amount_kzt: 39000, reference: 'п/п 77' })
  })

  it('blocks a zero amount and warns when it differs from the invoice', async () => {
    mockFetch({ [URL_]: () => json({ ok: true }) })
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <ApproveForm mode="payment" applicationId={3} defaultSlug="primer" config={config} invoiceNo="NOX-2026-00001"
          invoiceAmount={39000} tariffCoins={100000} onDone={() => undefined} />
      </MemoryRouter>,
    )
    const amount = screen.getByLabelText('Получено, ₸')
    await user.clear(amount)
    await user.type(amount, '0')
    expect(screen.getByRole('button', { name: 'Отметить оплату и создать бота' })).toBeDisabled()
    await user.clear(amount)
    await user.type(amount, '20000')
    expect(screen.getByText('Сумма отличается от счёта')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Отметить оплату и создать бота' })).toBeEnabled()
  })
})
