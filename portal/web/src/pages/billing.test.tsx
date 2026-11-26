import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { bodyOf, json, mockFetch } from '../test/utils'
import { ApproveForm } from './staff/ApproveForm'
import { TariffPicker } from './TariffPicker'

const TARIFFS = [
  { code: 'start', name: 'Старт', price_kzt: 15000, coins: 30000, description: 'a' },
  { code: 'business', name: 'Бизнес', price_kzt: 39000, coins: 100000, description: 'b' },
]

describe('TariffPicker', () => {
  it('lists tariffs and issues the invoice for the chosen one', async () => {
    const fetch = mockFetch({
      'GET /api/tariffs': () => json(TARIFFS),
      'POST /api/me/tariff': () => json({ status: 'awaiting_payment', invoice_no: 'NOX-2026-00001' }),
    })
    const onChosen = vi.fn()
    const user = userEvent.setup()
    render(<TariffPicker onChosen={onChosen} />)
    const business = await screen.findByRole('radio', { name: /Бизнес/ })
    expect(business).toHaveTextContent('39 000 ₸')
    const submit = screen.getByRole('button', { name: 'Выбрать тариф и получить счёт' })
    expect(submit).toBeDisabled()
    await user.click(business)
    expect(business).toHaveAttribute('aria-checked', 'true')
    await user.click(submit)
    await waitFor(() => expect(onChosen).toHaveBeenCalled())
    expect(bodyOf(fetch, 'POST /api/me/tariff')).toEqual({ tariff: 'business' })
  })

  it('changing the tariff needs a different choice', async () => {
    mockFetch({ 'GET /api/tariffs': () => json(TARIFFS) })
    render(<TariffPicker current="start" onChosen={() => undefined} />)
    await screen.findByRole('radio', { name: /Старт/ })
    expect(screen.getByRole('button', { name: 'Сменить тариф и обновить счёт' })).toBeDisabled()
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
      <ApproveForm mode="payment" applicationId={3} defaultSlug="primer" config={config} invoiceNo="NOX-2026-00001"
        invoiceAmount={39000} tariffCoins={100000} onDone={() => undefined} />,
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
