import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { App } from '../../App'
import { BotSetupStep } from './BotSetupStep'
import { FakeEventSource, callsTo, jsonResponse, mockApi } from '../../test/utils'
import { BOT } from '../../test/fixtures'
import type { BotSettings, TemplateMeta } from '../../api/types'

const TEMPLATES: TemplateMeta[] = [
  {
    id: 'beauty_salon',
    name: 'Салон красоты / барбершоп',
    description: 'Запись к мастерам',
    fields: [
      { key: 'business_name', label: 'Название', type: 'text', required: true, placeholder: '', hint: '' },
      { key: 'phone', label: 'Телефон для связи', type: 'phone', required: false, placeholder: '', hint: '' },
    ],
    defaults: { name: 'Администратор' },
  },
  {
    id: 'generic',
    name: 'Универсальный бизнес',
    description: 'Любой бизнес',
    fields: [{ key: 'business_name', label: 'Название', type: 'text', required: true, placeholder: '', hint: '' }],
    defaults: {},
  },
]

async function fillOwner() {
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText('E-mail'), 'owner@club.kz')
  await user.type(screen.getByLabelText('Пароль'), 'owner-pass-123')
  await user.type(screen.getByLabelText('Повторите пароль'), 'owner-pass-123')
  await user.click(screen.getByRole('button', { name: 'Создать и продолжить' }))
}

describe('Setup wizard', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', FakeEventSource)
    sessionStorage.clear()
  })

  it('shows the new step order without a template step', async () => {
    mockApi({ 'GET /api/setup/state': { needs_setup: true } })
    render(
      <MemoryRouter initialEntries={['/setup']}>
        <App />
      </MemoryRouter>,
    )
    const steps = await screen.findByRole('list', { name: 'Шаги первоначальной настройки' })
    expect(steps.textContent).toBe('1Владелец2Ваш бот3WhatsApp4Готово')
  })

  it('sends the setup token from the URL and explains a missing/invalid token', async () => {
    window.history.replaceState(null, '', '/setup?token=one-time-123')
    const fetchMock = mockApi({
      'GET /api/setup/state': { needs_setup: true },
      'POST /api/setup/owner': () => jsonResponse({ error: { code: 'setup_token_invalid', message: 'bad token' } }, 403),
    })
    render(
      <MemoryRouter initialEntries={['/setup']}>
        <App />
      </MemoryRouter>,
    )
    await fillOwner()
    expect(window.location.search).toBe('') // token removed from the address bar
    expect(sessionStorage.getItem('iler_setup_token')).toBe('one-time-123')
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/setup/owner')).toHaveLength(1))
    const init = callsTo(fetchMock, 'POST', '/api/setup/owner')[0][1] as RequestInit
    expect(new Headers(init.headers).get('X-ILER-Setup-Token')).toBe('one-time-123')
    const alerts = await screen.findAllByRole('alert')
    const inline = alerts.find((el) => el.tagName === 'P')
    expect(inline).toHaveTextContent('Откройте ссылку из установщика (она содержит одноразовый токен)')
  })
})

describe('Owner from the Nox portal (one account for the cabinet and the panel)', () => {
  const OWNER = { id: 1, email: 'owner@salon.kz', name: 'Айгерим', role: 'owner' }

  beforeEach(() => {
    vi.stubGlobal('EventSource', FakeEventSource)
    sessionStorage.clear()
    window.history.replaceState(null, '', '/')
  })

  it('login page from the cabinet button: e-mail pre-filled, hint, no auto-login, then the wizard', async () => {
    window.history.replaceState(null, '', '/login?email=owner%40salon.kz')
    const user = userEvent.setup()
    let loggedIn = false
    const fetchMock = mockApi({
      'GET /api/setup/state': () => ({ needs_setup: false, wizard_pending: true }),
      'GET /api/auth/me': () =>
        loggedIn ? OWNER : jsonResponse({ error: { code: 'unauthorized', message: 'Not signed in' } }, 401),
      'POST /api/auth/login': () => {
        loggedIn = true
        return OWNER
      },
      'GET /api/templates': TEMPLATES,
      'GET /api/settings/bot': BOT,
    })
    render(
      <MemoryRouter initialEntries={['/login']}>
        <App />
      </MemoryRouter>,
    )
    expect(await screen.findByTestId('cabinet-login-hint')).toHaveTextContent('Войдите e-mail и паролем от личного кабинета.')
    expect(screen.getByLabelText('E-mail')).toHaveValue('owner@salon.kz')
    expect(window.location.search).toBe('') // the e-mail leaves the address bar
    expect(callsTo(fetchMock, 'POST', '/api/auth/login')).toHaveLength(0) // prefill only
    await user.type(screen.getByLabelText('Пароль'), 'cabinet-pass-123')
    await user.click(screen.getByRole('button', { name: 'Войти' }))
    const body = JSON.parse(String((callsTo(fetchMock, 'POST', '/api/auth/login')[0][1] as RequestInit).body))
    expect(body).toEqual({ email: 'owner@salon.kz', password: 'cabinet-pass-123' })
    // straight into the wizard, without the owner step
    const steps = await screen.findByRole('list', { name: 'Шаги первоначальной настройки' })
    expect(steps.textContent).toBe('1Ваш бот2WhatsApp3Готово')
    expect(screen.queryByText('Создайте учётную запись владельца')).toBeNull()
    expect(await screen.findByText('Ваш бот', { selector: 'h1, h2' })).toBeInTheDocument()
  })

  it('plain login page has no cabinet hint', async () => {
    mockApi({
      'GET /api/setup/state': { needs_setup: false },
      'GET /api/auth/me': () => jsonResponse({ error: { code: 'unauthorized', message: 'Not signed in' } }, 401),
    })
    window.history.replaceState(null, '', '/login')
    render(
      <MemoryRouter initialEntries={['/login']}>
        <App />
      </MemoryRouter>,
    )
    expect(await screen.findByLabelText('E-mail')).toHaveValue('')
    expect(screen.queryByTestId('cabinet-login-hint')).toBeNull()
  })

  it('pending wizard: opened from any page, company already in "Название компании", finish closes it', async () => {
    const user = userEvent.setup()
    let pending = true
    const fetchMock = mockApi({
      'GET /api/setup/state': () => ({ needs_setup: false, wizard_pending: pending }),
      'GET /api/auth/me': OWNER,
      'GET /api/templates': TEMPLATES,
      // core put the company from the application into the template field business_name
      'GET /api/settings/bot': { ...BOT, template_values: { business_name: 'Салон Aru' } },
      'POST /api/setup/finish': () => {
        pending = false
        return { needs_setup: false, wizard_pending: false }
      },
    })
    render(
      <MemoryRouter initialEntries={['/bookings']}>
        <App />
      </MemoryRouter>,
    )
    const steps = await screen.findByRole('list', { name: 'Шаги первоначальной настройки' })
    expect(steps.textContent).toBe('1Ваш бот2WhatsApp3Готово')
    expect(await screen.findByLabelText('Название компании')).toHaveValue('Салон Aru')
    expect(screen.queryByText('Подключите языковую модель')).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Настрою позже' }))
    await user.click(await screen.findByRole('button', { name: 'Подключу позже' }))
    expect(callsTo(fetchMock, 'POST', '/api/setup/finish')).toHaveLength(0)
    await user.click(await screen.findByRole('button', { name: 'Перейти в админку' }))
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/setup/finish')).toHaveLength(1))
    await waitFor(() => expect(screen.queryByRole('list', { name: 'Шаги первоначальной настройки' })).toBeNull())
  })
})

describe('BotSetupStep', () => {
  function saving(box: { saved: BotSettings | null }) {
    return (init: RequestInit) => {
      box.saved = JSON.parse(String(init.body)) as BotSettings
      return box.saved
    }
  }

  it('universal template first; name only: renders with template defaults and starts the bot', async () => {
    const user = userEvent.setup()
    const box: { saved: BotSettings | null } = { saved: null }
    const fetchMock = mockApi({
      'GET /api/templates': TEMPLATES,
      'GET /api/settings/bot': BOT,
      'POST /api/templates/generic/render': (init: RequestInit) => {
        const { values } = JSON.parse(String(init.body)) as { values: Record<string, string> }
        return { prompt: `Ты — консультант «${values.business_name}».`, missing: [] }
      },
      'PUT /api/settings/bot': saving(box),
    })
    const onDone = vi.fn()
    render(<BotSetupStep onDone={onDone} />)
    const launch = await screen.findByRole('button', { name: 'Запустить бота' })
    expect(screen.getByRole('radio', { name: /Универсальный бизнес/ })).toBeChecked()
    await user.click(launch)
    expect(await screen.findByText('Укажите название компании')).toBeInTheDocument()
    expect(callsTo(fetchMock, 'PUT', '/api/settings/bot')).toHaveLength(0)

    await user.type(screen.getByLabelText('Название компании'), 'Мастер')
    await user.click(launch)
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(true))
    expect(callsTo(fetchMock, 'POST', '/api/templates/generic/ai-fill')).toHaveLength(0)
    expect(box.saved).toMatchObject({
      enabled: true,
      template_id: 'generic',
      template_values: { business_name: 'Мастер' },
      prompt: 'Ты — консультант «Мастер».',
      prompt_edited: false,
    })
  })

  it('a story goes to the AI fill and its prompt is saved with the template defaults', async () => {
    const user = userEvent.setup()
    const box: { saved: BotSettings | null } = { saved: null }
    const fetchMock = mockApi({
      'GET /api/templates': TEMPLATES,
      'GET /api/settings/bot': BOT,
      'POST /api/templates/beauty_salon/ai-fill': {
        values: { business_name: 'Борода', phone: '+7 701 000 00 00' },
        prompt: 'Ты — администратор «Борода». Телефон: +7 701 000 00 00',
        missing: [],
      },
      'PUT /api/settings/bot': saving(box),
    })
    const onDone = vi.fn()
    render(<BotSetupStep onDone={onDone} />)
    await user.click(await screen.findByRole('radio', { name: /Салон красоты/ }))
    await user.type(screen.getByLabelText('Название компании'), 'Борода')
    await user.type(screen.getByLabelText(/Расскажите о бизнесе/), 'Барбершоп, звоните +7 701 000 00 00')
    await user.click(screen.getByRole('button', { name: 'Собрать промпт и запустить бота' }))
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(true))
    const body = JSON.parse(String((callsTo(fetchMock, 'POST', '/api/templates/beauty_salon/ai-fill')[0][1] as RequestInit).body))
    expect(body).toEqual({ business_name: 'Борода', description: 'Барбершоп, звоните +7 701 000 00 00' })
    expect(box.saved).toMatchObject({
      enabled: true,
      name: 'Администратор',
      template_id: 'beauty_salon',
      template_values: { business_name: 'Борода', phone: '+7 701 000 00 00' },
      prompt: 'Ты — администратор «Борода». Телефон: +7 701 000 00 00',
    })
  })

  it('an AI failure keeps the story and offers to start without it', async () => {
    const user = userEvent.setup()
    mockApi({
      'GET /api/templates': TEMPLATES,
      'GET /api/settings/bot': BOT,
      'POST /api/templates/generic/ai-fill': () =>
        jsonResponse({ error: { code: 'coins_exhausted', message: 'Коины ILER закончились' } }, 409),
    })
    const onDone = vi.fn()
    render(<BotSetupStep onDone={onDone} />)
    await user.type(await screen.findByLabelText('Название компании'), 'Мастер')
    await user.type(screen.getByLabelText(/Расскажите о бизнесе/), 'Ремонт квартир под ключ')
    await user.click(screen.getByRole('button', { name: 'Собрать промпт и запустить бота' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Коины ILER закончились. Можно запустить бота без рассказа')
    expect(screen.getByLabelText(/Расскажите о бизнесе/)).toHaveValue('Ремонт квартир под ключ')
    expect(onDone).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Запустить без рассказа' }))
    expect(screen.getByRole('button', { name: 'Запустить бота' })).toBeInTheDocument()
  })
})
