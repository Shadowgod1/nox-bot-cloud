import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { App } from '../../App'
import { BotSetupStep } from './BotSetupStep'
import { LlmStep } from './Setup'
import { FakeEventSource, callsTo, jsonResponse, mockApi } from '../../test/utils'
import { BOT } from '../../test/fixtures'
import type { BotSettings, LlmSettings, TemplateMeta } from '../../api/types'

const LLM: LlmSettings = {
  provider: 'openai',
  openai_key: null,
  openai_model: 'gpt-4.1-mini',
  openai_fallback_model: null,
  gemini_key: null,
  gemini_model: 'gemini-2.5-flash',
  anthropic_key: null,
  anthropic_model: 'claude-opus-5-5',
  anthropic_effort: 'low',
  emergency_provider: null,
}

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
    expect(steps.textContent).toBe('1Владелец2Языковая модель3WhatsApp4Настройка бота5Готово')
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

describe('LlmStep', () => {
  it('connects Anthropic (Claude) with model and effort, saving only after a successful check', async () => {
    const user = userEvent.setup()
    const fetchMock = mockApi({
      'GET /api/settings/llm': LLM,
      'POST /api/settings/llm/test': { ok: true },
      'PUT /api/settings/llm': (init: RequestInit) => JSON.parse(String(init.body)),
    })
    render(<LlmStep onDone={vi.fn()} />)

    await user.click(await screen.findByRole('radio', { name: /Anthropic \(Claude\)/ }))
    const model = screen.getByLabelText('Модель Claude')
    expect(model).toHaveValue('claude-opus-5-5')
    expect(screen.getByText('claude-sonnet-5-5 — дешевле, claude-haiku-5-5 — самый дешёвый')).toBeInTheDocument()
    expect(screen.getByLabelText('Глубина размышлений')).toHaveValue('low')

    await user.type(screen.getByLabelText('Ключ API'), 'sk-ant-test-1')
    await user.clear(model)
    await user.type(model, 'claude-sonnet-5-5')
    await user.selectOptions(screen.getByLabelText('Глубина размышлений'), 'Средняя')
    await user.click(screen.getByRole('button', { name: 'Проверить ключ' }))

    await waitFor(() => expect(callsTo(fetchMock, 'PUT', '/api/settings/llm')).toHaveLength(1))
    const tested = JSON.parse(String((callsTo(fetchMock, 'POST', '/api/settings/llm/test')[0][1] as RequestInit).body))
    expect(tested).toMatchObject({
      provider: 'anthropic',
      anthropic_key: 'sk-ant-test-1',
      anthropic_model: 'claude-sonnet-5-5',
      anthropic_effort: 'medium',
      openai_key: null,
    })
  })

  it('hides the Claude fields for other providers', async () => {
    mockApi({ 'GET /api/settings/llm': LLM })
    render(<LlmStep onDone={vi.fn()} />)
    await screen.findByLabelText('Ключ API')
    expect(screen.queryByLabelText('Модель Claude')).not.toBeInTheDocument()
  })
})

describe('BotSetupStep', () => {
  it('requires fields, renders the prompt, lets it be edited and saves with template defaults', async () => {
    const user = userEvent.setup()
    let saved: BotSettings | null = null
    const fetchMock = mockApi({
      'GET /api/templates': TEMPLATES,
      'GET /api/settings/bot': BOT,
      'POST /api/templates/beauty_salon/render': (init: RequestInit) => {
        const { values } = JSON.parse(String(init.body)) as { values: Record<string, string> }
        return { prompt: `Ты — администратор «${values.business_name}».`, missing: [] }
      },
      'PUT /api/settings/bot': (init: RequestInit) => {
        saved = JSON.parse(String(init.body)) as BotSettings
        return saved
      },
    })
    const onDone = vi.fn()
    render(<BotSetupStep onDone={onDone} />)

    const build = await screen.findByRole('button', { name: 'Собрать промпт' })
    expect(screen.getByRole('button', { name: 'Сохранить и включить бота' })).toBeDisabled()
    await user.click(build)
    expect(await screen.findByText('Заполните это поле')).toBeInTheDocument()
    expect(callsTo(fetchMock, 'POST', '/api/templates/beauty_salon/render')).toHaveLength(0)

    await user.type(screen.getByLabelText('Название'), 'Борода')
    await user.click(build)
    const editor = await screen.findByLabelText('Промпт бота')
    expect(editor).toHaveValue('Ты — администратор «Борода».')
    const body = JSON.parse(String((callsTo(fetchMock, 'POST', '/api/templates/beauty_salon/render')[0][1] as RequestInit).body))
    expect(body).toEqual({ values: { business_name: 'Борода', phone: '' } })

    await user.type(editor, ' Пиши кратко.')
    await user.click(screen.getByRole('button', { name: 'Сохранить и включить бота' }))
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(true))
    expect(saved).toMatchObject({
      enabled: true,
      name: 'Администратор',
      template_id: 'beauty_salon',
      template_values: { business_name: 'Борода', phone: '' },
      prompt: 'Ты — администратор «Борода». Пиши кратко.',
      prompt_edited: true,
    })
  })
})
