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
  openai_model: 'gpt-4.1-mini',
  openai_fallback_model: null,
  gemini_model: 'gemini-2.5-flash',
  anthropic_model: 'claude-sonnet-5-5',
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
    expect(steps.textContent).toBe('1Владелец2Языковая модель3WhatsApp4Настройка бота5Проверьте бота6Готово')
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
  it('shows only the providers ILER has keys for, without a key field', async () => {
    mockApi({
      'GET /api/settings/llm': LLM,
      'GET /api/settings/llm/providers': { providers: ['anthropic', 'gemini'] },
      'POST /api/settings/llm/models': { ok: true, models: [] },
    })
    render(<LlmStep onDone={vi.fn()} />)
    // the stored provider (openai) has no ILER key: the first available one is preselected
    expect(await screen.findByRole('radio', { name: /Anthropic \(Claude\)/ })).toBeChecked()
    expect(screen.getByRole('radio', { name: /Google Gemini/ })).not.toBeChecked()
    expect(screen.queryByRole('radio', { name: /OpenAI/ })).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/Ключ/)).toBeNull()
    expect(screen.getByText(/подключение ILER/)).toBeInTheDocument()
  })

  it('tells the user to contact ILER support when no keys are configured', async () => {
    const user = userEvent.setup()
    const onDone = vi.fn()
    mockApi({ 'GET /api/settings/llm': LLM, 'GET /api/settings/llm/providers': { providers: [] } })
    render(<LlmStep onDone={onDone} />)
    expect(await screen.findByText(/Ключи ILER не настроены\. Обратитесь в поддержку ILER/)).toBeInTheDocument()
    expect(screen.queryByRole('radio')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Проверить подключение' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Настроить позже' }))
    expect(onDone).toHaveBeenCalled()
  })

  it('connects Claude with Haiku and effort, saving only after a successful check', async () => {
    const user = userEvent.setup()
    const fetchMock = mockApi({
      'GET /api/settings/llm': LLM,
      'GET /api/settings/llm/providers': { providers: ['anthropic', 'openai'] },
      'POST /api/settings/llm/models': (init: RequestInit) =>
        JSON.parse(String(init.body)).provider === 'anthropic'
          ? { ok: true, models: ['claude-sonnet-5-5', 'claude-haiku-5-5'] }
          : { ok: true, models: ['gpt-5.4-mini'] },
      'POST /api/settings/llm/test': { ok: true },
      'PUT /api/settings/llm': (init: RequestInit) => JSON.parse(String(init.body)),
    })
    render(<LlmStep onDone={vi.fn()} />)

    // the stored provider (openai) is available: it is preselected
    expect(await screen.findByRole('radio', { name: /OpenAI/ })).toBeChecked()
    await user.click(screen.getByRole('radio', { name: /Anthropic \(Claude\)/ }))
    const model = screen.getByLabelText('Модель')
    expect(model).toHaveValue('claude-sonnet-5-5')
    expect([...(model as HTMLSelectElement).options].map((o) => o.value)).toEqual(['claude-sonnet-5-5', 'claude-haiku-5-5'])
    expect(screen.getByLabelText('Глубина размышлений')).toHaveValue('low')

    await user.selectOptions(model, 'claude-haiku-5-5')
    await user.selectOptions(screen.getByLabelText('Глубина размышлений'), 'Средняя')
    await user.click(screen.getByRole('button', { name: 'Проверить подключение' }))

    await waitFor(() => expect(callsTo(fetchMock, 'PUT', '/api/settings/llm')).toHaveLength(1))
    const tested = JSON.parse(String((callsTo(fetchMock, 'POST', '/api/settings/llm/test')[0][1] as RequestInit).body))
    expect(tested).toMatchObject({ provider: 'anthropic', anthropic_model: 'claude-haiku-5-5', anthropic_effort: 'medium' })
    expect(Object.keys(tested).filter((k) => k.endsWith('_key'))).toEqual([])
  })

  it('loads OpenAI models on selection, preselects a recommended one, checks without a key and saves the final choice', async () => {
    const user = userEvent.setup()
    const onDone = vi.fn()
    const fetchMock = mockApi({
      'GET /api/settings/llm': LLM,
      'GET /api/settings/llm/providers': { providers: ['openai'] },
      'POST /api/settings/llm/models': { ok: true, models: ['gpt-5.5', 'gpt-5.4-nano'] },
      'POST /api/settings/llm/test': { ok: true },
      'PUT /api/settings/llm': (init: RequestInit) => JSON.parse(String(init.body)),
    })
    render(<LlmStep onDone={onDone} />)
    // gpt-5.4-mini is not available through ILER: the next recommended listed model is preselected, without pressing the button
    await waitFor(() => expect(screen.getByLabelText('Модель')).toHaveValue('gpt-5.4-nano'))
    expect(screen.getByRole('option', { name: 'gpt-5.5' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Глубина размышлений')).not.toBeInTheDocument()
    expect(callsTo(fetchMock, 'POST', '/api/settings/llm/test')).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: 'Проверить подключение' }))
    await waitFor(() => expect(callsTo(fetchMock, 'PUT', '/api/settings/llm')).toHaveLength(1))
    // models -> test -> put, in that order
    const order = fetchMock.mock.calls
      .map(([url, init]) => `${((init as RequestInit | undefined)?.method ?? 'GET').toUpperCase()} ${String(url)}`)
      .filter((c) => !c.startsWith('GET'))
    expect(order.slice(-3)).toEqual(['POST /api/settings/llm/models', 'POST /api/settings/llm/test', 'PUT /api/settings/llm'])
    const tested = JSON.parse(String((callsTo(fetchMock, 'POST', '/api/settings/llm/test')[0][1] as RequestInit).body))
    expect(tested).toMatchObject({ provider: 'openai', openai_model: 'gpt-5.4-nano' })
    expect(Object.keys(tested).filter((k) => k.endsWith('_key'))).toEqual([])

    await user.selectOptions(screen.getByLabelText('Модель'), 'gpt-5.5')
    await user.click(screen.getByRole('button', { name: 'Продолжить' }))
    await waitFor(() => expect(onDone).toHaveBeenCalled())
    const puts = callsTo(fetchMock, 'PUT', '/api/settings/llm')
    expect(JSON.parse(String((puts[puts.length - 1][1] as RequestInit).body))).toMatchObject({ openai_model: 'gpt-5.5' })
  })

  it('shows the provider error when the connection check fails', async () => {
    const user = userEvent.setup()
    const fetchMock = mockApi({
      'GET /api/settings/llm': LLM,
      'GET /api/settings/llm/providers': { providers: ['openai', 'gemini'] },
      'POST /api/settings/llm/models': { ok: false, models: [], error: 'gemini HTTP 400: API key not valid' },
    })
    render(<LlmStep onDone={vi.fn()} />)
    await user.click(await screen.findByRole('radio', { name: /Google Gemini/ }))
    await user.click(screen.getByRole('button', { name: 'Проверить подключение' }))
    expect(await screen.findByText(/API key not valid/)).toBeInTheDocument()
    expect(callsTo(fetchMock, 'POST', '/api/settings/llm/test')).toHaveLength(0)
    expect(callsTo(fetchMock, 'PUT', '/api/settings/llm')).toHaveLength(0)
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

  it('prefills typical FAQ and services from the template and saves the edited price list', async () => {
    const user = userEvent.setup()
    let saved: BotSettings | null = null
    const salon: TemplateMeta = {
      id: 'beauty_salon',
      name: 'Салон красоты / барбершоп',
      description: '',
      fields: [
        { key: 'business_name', label: 'Название', type: 'text', required: true, placeholder: '', hint: '' },
        { key: 'faq', label: 'Частые вопросы и ответы', type: 'textarea', required: false, placeholder: '', hint: '', default: 'Как записаться? — Напишите нам.' },
      ],
      defaults: {
        name: 'Администратор',
        services: [
          { name: 'Маникюр классический', price: 'от … ₸', duration_min: 60 },
          { name: 'Укладка', price: 'от … ₸', duration_min: 45 },
        ],
      },
    }
    mockApi({
      'GET /api/templates': [salon],
      'GET /api/settings/bot': BOT,
      'POST /api/templates/beauty_salon/render': { prompt: 'Ты — администратор.', missing: [] },
      'PUT /api/settings/bot': (init: RequestInit) => {
        saved = JSON.parse(String(init.body)) as BotSettings
        return saved
      },
    })
    const onDone = vi.fn()
    render(<BotSetupStep onDone={onDone} />)
    expect(await screen.findByLabelText('Частые вопросы и ответы')).toHaveValue('Как записаться? — Напишите нам.')
    expect(screen.getByText(/Не заполнено цен: 2/)).toBeInTheDocument()
    await user.clear(screen.getByLabelText('Маникюр классический: цена'))
    await user.type(screen.getByLabelText('Маникюр классический: цена'), '5000')
    await user.tab()
    await user.click(screen.getByRole('button', { name: 'Удалить «Укладка»' }))
    await user.type(screen.getByLabelText('Название'), 'Aru')
    await user.click(screen.getByRole('button', { name: 'Собрать промпт' }))
    await screen.findByLabelText('Промпт бота')
    await user.click(screen.getByRole('button', { name: 'Сохранить и включить бота' }))
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(true))
    expect(saved).toMatchObject({
      services: [{ name: 'Маникюр классический', price: '5 000 ₸', duration_min: 60 }],
      template_values: { business_name: 'Aru', faq: 'Как записаться? — Напишите нам.' },
    })
  })
})
