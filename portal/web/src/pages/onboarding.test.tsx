import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { daysLeft, dialogsText, plural, resetPublicConfig } from '../api'
import { fillRequisites, Markdown } from '../legal/markdown'
import { slugFromCompany } from '../slug'
import { bodyOf, json, mockFetch, renderAt } from '../test/utils'
import { Cabinet } from './Cabinet'
import { CoinsPanel } from './CoinsPanel'
import { Legal } from './Legal'
import { Register } from './Register'
import { ApplicationView } from './staff/ApplicationView'
import { TariffPicker } from './TariffPicker'

const SUPPORT = { phone: '+7 700 123 45 67', whatsapp: '77001234567', whatsapp_url: 'https://wa.me/77001234567', email: 'info@noxsw.kz' }
const NO_SUPPORT = { phone: '', whatsapp: '', whatsapp_url: '', email: 'info@noxsw.kz' }
const CONFIG = {
  instance_domain: 'example.kz', online_payments: false, online_methods: [], free_days: 14, coins_per_dialog: 37,
  support: SUPPORT, legal_date: '', seller: { name: '', bin: '', address: '', bank: '', iik: '', bik: '', kbe: '', phone: '' },
}

beforeEach(() => {
  resetPublicConfig()
  vi.restoreAllMocks()
})

describe('helpers', () => {
  it('plural, dialogs text, days left', () => {
    expect([1, 2, 5, 11, 21, 22, 112].map((n) => plural(n, ['день', 'дня', 'дней']))).toEqual(['день', 'дня', 'дней', 'дней', 'день', 'дня', 'дней'])
    expect(dialogsText(800)).toBe('≈ 800 диалогов')
    expect(dialogsText(1600)).toBe('≈ 1 600 диалогов')
    expect(dialogsText(1)).toBe('≈ 1 диалог')
    const now = Date.parse('2026-11-26T10:00:00Z')
    expect(daysLeft('2026-11-29T09:00:00Z', now)).toBe(3)
    expect(daysLeft('2026-11-26T11:00:00Z', now)).toBe(1)
    expect(daysLeft('2026-11-25T11:00:00Z', now)).toBe(0)
    expect(daysLeft(null, now)).toBe(0)
  })

  it('bot address from the company name', () => {
    expect(slugFromCompany('ТОО «Салон Аяна»')).toBe('salon-ayana')
    expect(slugFromCompany('ИП Жанна Құрманова')).toBe('zhanna-kurmanova')
    expect(slugFromCompany('"Beauty Room" ТОО')).toBe('beauty-room')
    expect(slugFromCompany('ООО Щука и Ёж')).toBe('schuka-i-ezh')
    expect(slugFromCompany('7 небо')).toBe('bot-7-nebo')
    expect(slugFromCompany('Я')).toBe('ya-bot')
    expect(slugFromCompany('ТОО')).toBe('')
    expect(slugFromCompany('Типография')).toBe('tipografiya')
    const long = slugFromCompany('Очень длинное название салона красоты и спа на набережной')
    expect(long.length).toBeLessThanOrEqual(32)
    expect(long).toMatch(/^[a-z][a-z0-9-]{1,30}[a-z0-9]$/)
  })
})

describe('Register: bot address and consent', () => {
  it('fills the address from the company name until it is edited, offers a free variant', async () => {
    const fetch = mockFetch({
      'GET /api/public-config': () => json(CONFIG),
      'GET /api/slug-check': (url) =>
        url.includes('salon-ayana-2')
          ? json({ slug: 'salon-ayana-2', available: true, message: null })
          : json({ slug: 'salon-ayana', available: false, message: 'этот адрес уже занят', suggestion: 'salon-ayana-2' }),
    })
    const user = userEvent.setup()
    renderAt('/register', <Register />)
    const slug = screen.getByRole('textbox', { name: 'Адрес бота' })
    await user.type(screen.getByLabelText('Название компании'), 'ТОО «Салон Аяна»')
    expect(slug).toHaveValue('salon-ayana')
    expect(screen.getByText(/Клиенты этот адрес не видят/)).toBeInTheDocument()
    expect(screen.getByText(/Подставили из названия/)).toBeInTheDocument()
    await user.click(screen.getByLabelText('Контактное лицо')) // blur -> availability check
    expect(await screen.findByText('этот адрес уже занят')).toBeInTheDocument()
    const hint = screen.getByTestId('slug-suggestion')
    expect(hint).toHaveTextContent('salon-ayana-2')
    await user.click(within(hint).getByRole('button', { name: 'Взять его' }))
    expect(slug).toHaveValue('salon-ayana-2')
    expect(screen.queryByText('этот адрес уже занят')).toBeNull()
    // edited by hand: the company name no longer changes it
    await user.clear(slug)
    await user.type(slug, 'ayana')
    await user.type(screen.getByLabelText('Название компании'), ' plus')
    expect(slug).toHaveValue('ayana')
    expect(fetch).toHaveBeenCalled()
  })

  it('consent links open the offer and the privacy policy in a new tab', async () => {
    mockFetch({ 'GET /api/public-config': () => json(CONFIG) })
    renderAt('/register', <Register />)
    const offer = screen.getByRole('link', { name: 'условия оферты' })
    const privacy = screen.getByRole('link', { name: 'политике конфиденциальности' })
    expect(offer).toHaveAttribute('href', '/offer')
    expect(offer).toHaveAttribute('target', '_blank')
    expect(privacy).toHaveAttribute('href', '/privacy')
    expect(privacy).toHaveAttribute('target', '_blank')
  })
})

describe('coins as dialogs', () => {
  it('tariff cards and the Free row show dialogs', async () => {
    mockFetch({
      'GET /api/tariffs': () =>
        json([
          { code: 'start', name: 'Старт', price_kzt: 70000, coins: 30000, description: 'a', dialogs: 800 },
          { code: 'free', name: 'Free (тест)', price_kzt: 0, coins: 2000, description: 'b', available: true, dialogs: 50 },
        ]),
      'GET /api/public-config': () => json(CONFIG),
    })
    render(<TariffPicker onChosen={() => undefined} />)
    const start = await screen.findByRole('radio', { name: /Старт/ })
    expect(start).toHaveTextContent('≈ 800 диалогов в месяц')
    expect(screen.getByRole('radio', { name: /Free/ })).toHaveTextContent('2 000 коинов — ≈ 50 диалогов на весь тест')
  })

  it('the coin balance shows dialogs left', async () => {
    mockFetch({
      'GET /api/me/usage': () =>
        json({
          balance: { coins_left: 1850, monthly_limit: 2000, monthly_coins: 2000, topup_coins: 0, spent: 150, period_start: null, period_end: null, status: 'active', dialogs_left: 50 },
          by_day: [], by_model: [],
        }),
      'GET /api/me/invoices': () => json([]),
      'GET /api/public-config': () => json(CONFIG),
    })
    render(
      <MemoryRouter>
        <CoinsPanel />
      </MemoryRouter>,
    )
    expect(await screen.findByTestId('dialogs-left')).toHaveTextContent('≈ 50 диалогов')
    expect(screen.getByTestId('coins-left')).toHaveTextContent('1 850')
  })
})

describe('Cabinet of an active Free bot', () => {
  const inDays = (d: number) => new Date(Date.now() + d * 86_400_000 - 3_600_000).toISOString()
  const ME = {
    kind: 'client', email: 'o@example.kz', company: 'Салон', contact_name: 'А', phone: '+7 701 000 00 00', slug: 'salon',
    bot_host: 'salon.example.kz', status: 'active', admin_url: 'https://salon.example.kz', reject_reason: null,
    setup_link_available: true, tariff: 'free', invoice_no: null, invoice_amount: null, created_at: '2026-11-20T00:00:00Z',
    free_until: inDays(5), setup_help_requested_at: null, setup_help_contacted_at: null,
  }

  function routes(me: object, config: object = CONFIG) {
    return {
      'GET /api/me': () => json(me),
      'GET /api/public-config': () => json(config),
      'GET /api/me/invoices': () => json([]),
      'GET /api/tariffs': () => json([]),
    }
  }

  it('panel button first, two places explained, trial days, first-login link, FAQ and help', async () => {
    mockFetch(routes(ME))
    renderAt('/cabinet', <Cabinet />)
    const open = await screen.findByRole('link', { name: /Открыть панель бота/ })
    expect(open).toHaveAttribute('href', 'https://salon.example.kz')
    expect(open).toHaveAttribute('target', '_blank')
    const links = screen.getAllByRole('link')
    expect(links.indexOf(open)).toBeLessThan(links.findIndex((l) => l.textContent?.includes('WhatsApp')))
    expect(screen.getByText('Здесь — тариф, оплата и коины. Настройка бота и переписка — в панели бота.')).toBeInTheDocument()
    expect(screen.getByText(/Ссылка создаёт ваш логин и пароль в панели. Работает один раз — дальше входите по паролю. Копия на почте./)).toBeInTheDocument()
    const trial = screen.getByTestId('trial-info')
    expect(trial).toHaveTextContent('осталось 5 дней')
    expect(trial).toHaveTextContent('бот перестанет отвечать клиентам')
    expect(trial).toHaveTextContent('после оплаты бот продолжит с того же места')
    for (const q of ['Что такое коины?', 'Что будет после теста?', 'Как оплатить от ТОО или ИП?', 'Где настраивать бота?']) {
      expect(screen.getByText(q)).toBeInTheDocument()
    }
    expect(screen.getByText(/Закрывающие документы — по запросу менеджеру/)).toBeInTheDocument()
    const help = screen.getByTestId('help-card')
    await waitFor(() => expect(within(help).getByRole('link', { name: /WhatsApp/ })).toHaveAttribute('href', 'https://wa.me/77001234567'))
    expect(within(help).getByRole('link', { name: /\+7 700 123 45 67/ })).toHaveAttribute('href', 'tel:+77001234567')
  })

  it('setup help: one request, then "Запрос отправлен"; no price mentioned', async () => {
    const fetch = mockFetch({
      ...routes(ME),
      'POST /api/me/setup-help': () => json({ ...ME, setup_help_requested_at: '2026-11-26T05:00:00Z' }),
    })
    const user = userEvent.setup()
    renderAt('/cabinet', <Cabinet />)
    const block = await screen.findByTestId('setup-help')
    expect(block).toHaveTextContent('Не хотите разбираться? Менеджер подключит WhatsApp и заполнит услуги вместе с вами.')
    expect(block.textContent).not.toMatch(/₸|тенге|стоит/)
    await user.click(within(block).getByRole('button', { name: 'Попросить помочь с настройкой' }))
    expect(await within(block).findByText(/Запрос отправлен 26\.11\.2026/)).toBeInTheDocument()
    expect(within(block).queryByRole('button', { name: 'Попросить помочь с настройкой' })).toBeNull()
    expect(bodyOf(fetch, 'POST /api/me/setup-help')).toEqual({})
  })

  it('used setup link: "forgot the panel password" with contacts; no contacts configured -> e-mail only', async () => {
    mockFetch(routes({ ...ME, tariff: 'start', free_until: null, setup_link_available: false }, { ...CONFIG, support: NO_SUPPORT }))
    renderAt('/cabinet', <Cabinet />)
    const help = await screen.findByTestId('panel-password-help')
    expect(help).toHaveTextContent('Забыли пароль от панели? Напишите в поддержку')
    await waitFor(() => expect(within(help).getByRole('link', { name: 'info@noxsw.kz' })).toBeInTheDocument())
    expect(screen.queryByRole('link', { name: /WhatsApp/ })).toBeNull()
    expect(screen.queryByTestId('trial-info')).toBeNull()
  })
})

describe('staff: setup help in the application card', () => {
  it('shows the request and marks "связались"', async () => {
    const APP = {
      id: 7, company: 'Салон', contact_name: 'А', email: 'o@example.kz', phone: '+7 701 000 00 00', slug: 'salon', status: 'active',
      coins_limit: 2000, tariff: 'free', created_at: '2026-11-20T00:00:00Z', updated_at: '2026-11-20T00:00:00Z', bot_host: 'salon.example.kz',
      admin_url: null, port: null, reject_reason: null, last_error: null, email_verified_at: null, register_ip: null, invoice_no: null,
      invoice_amount: null, invoice_issued_at: null, setup_help_requested_at: '2026-11-26T05:00:00Z', setup_help_contacted_at: null,
      payments: [], jobs: [], audit: [],
    }
    let contacted = false
    const fetch = mockFetch({
      'GET /api/staff/auth/me': () => json({ kind: 'staff', id: 1, email: 'ops@example.kz', name: 'Ops' }),
      'GET /api/staff/config': () => json({ tariffs: [], coin_presets: [], instance_domain: 'example.kz', custom_key_supported: false }),
      'GET /api/staff/applications/7': () => json(contacted ? { ...APP, setup_help_contacted_at: '2026-11-26T06:00:00Z' } : APP),
      'POST /api/staff/applications/7/setup-help/contacted': () => {
        contacted = true
        return json({ ok: true })
      },
      'GET /api/public-config': () => json(CONFIG),
    })
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/staff/applications/7']}>
        <Routes>
          <Route path="/staff/applications/:id" element={<ApplicationView />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(await screen.findByText(/Просит помощь с настройкой с 26\.11\.2026/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Отметить, что связались' }))
    expect(await screen.findByTestId('setup-help-done')).toHaveTextContent('Связались 26.11.2026')
    expect(fetch.mock.calls.some(([u, i]) => String(u).endsWith('/setup-help/contacted') && (i as RequestInit).method === 'POST')).toBe(true)
  })
})

describe('legal pages', () => {
  it('draft banner without NOX_LEGAL_DATE, edition date with it; footer links to the documents', async () => {
    mockFetch({ 'GET /api/public-config': () => json(CONFIG) })
    const { unmount } = renderAt('/offer', <Legal doc="offer" />)
    expect(await screen.findByTestId('legal-edition')).toHaveTextContent('Черновик')
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Публичная оферта')
    const docs = screen.getByRole('navigation', { name: 'Документы' })
    expect(within(docs).getByRole('link', { name: 'Оферта' })).toHaveAttribute('href', '/offer')
    expect(within(docs).getByRole('link', { name: 'Возврат денег' })).toHaveAttribute('href', '/refund')
    expect(within(docs).getByRole('link', { name: 'Конфиденциальность' })).toHaveAttribute('href', '/privacy')
    unmount()
    resetPublicConfig()
    mockFetch({ 'GET /api/public-config': () => json({ ...CONFIG, legal_date: '01.12.2026' }) })
    renderAt('/privacy', <Legal doc="privacy" />)
    await waitFor(() => expect(screen.getByTestId('legal-edition')).toHaveTextContent('Редакция от 01.12.2026'))
    expect(screen.getByRole('table')).toBeInTheDocument()
  })

  it('requisites from the seller settings; unknown ones stay in brackets', () => {
    const md = '[Наименование], БИН [____]\nБанк: [____], ИИК [____], БИК [____], Кбе [__]\nТелефон: [____], срок [10] дней'
    const out = fillRequisites(md, { name: 'ТОО «Нокс»', bin: '123456789012', bank: '', iik: 'KZ12', bik: '', kbe: '17' }, '+7 700 1')
    expect(out).toBe('ТОО «Нокс», БИН 123456789012\nБанк: [____], ИИК KZ12, БИК [____], Кбе 17\nТелефон: +7 700 1, срок [10] дней')
    expect(fillRequisites(md, undefined)).toBe(md)
  })

  it('renders markdown without injecting HTML', () => {
    render(<Markdown source={'# Заголовок\n\nТекст **жирный** и <img src=x onerror=alert(1)> https://bot.noxsw.kz/offer.\n\n- один\n- два'} />)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Заголовок')
    expect(screen.getByText('жирный').tagName).toBe('STRONG')
    expect(document.querySelector('img')).toBeNull()
    expect(screen.getByText(/<img src=x/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'bot.noxsw.kz/offer' })).toHaveAttribute('href', 'https://bot.noxsw.kz/offer')
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
  })
})
