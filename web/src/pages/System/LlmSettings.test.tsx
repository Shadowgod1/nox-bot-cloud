import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { LlmSettingsPanel } from './LlmSettings'
import { callsTo, mockApi } from '../../test/utils'

const llm = {
  provider: 'openai',
  openai_key: '••••abcd',
  openai_model: 'gpt-4.1-mini',
  openai_fallback_model: null,
  gemini_key: null,
  gemini_model: 'gemini-2.5-flash',
  anthropic_key: '••••9876',
  anthropic_model: 'claude-sonnet-5-5',
  anthropic_effort: 'low',
  emergency_provider: null,
}

function lastPutBody(fetchMock: ReturnType<typeof mockApi>) {
  const calls = callsTo(fetchMock, 'PUT', '/api/settings/llm')
  const init = calls[calls.length - 1][1] as RequestInit
  return JSON.parse(String(init.body))
}

// Plan name: "BotSettings keeps masked key untouched on save" — the settings forms that hold
// secrets (LLM, SENET, SMTP) share SecretInput; this exercises it through the LLM form.
describe('settings form with masked secret', () => {
  it('keeps masked key untouched on save', async () => {
    const fetchMock = mockApi({
      'GET /api/settings/llm': llm,
      'PUT /api/settings/llm': (init: RequestInit) => ({ ...llm, ...JSON.parse(String(init.body)) }),
    })
    render(<LlmSettingsPanel />)
    const model = await screen.findByLabelText('Основная модель OpenAI')
    expect(screen.getByText('••••abcd')).toBeInTheDocument()

    await userEvent.selectOptions(model, 'gpt-5.4-nano')
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }))

    await waitFor(() => expect(callsTo(fetchMock, 'PUT', '/api/settings/llm')).toHaveLength(1))
    const body = lastPutBody(fetchMock)
    expect(body.openai_key).toBe('••••abcd')
    expect(body.openai_model).toBe('gpt-5.4-nano')
    expect(body.gemini_key).toBeNull()
  })

  it('sends the new key only when replaced, and null only when explicitly removed', async () => {
    const fetchMock = mockApi({
      'GET /api/settings/llm': llm,
      'PUT /api/settings/llm': (init: RequestInit) => ({ ...llm, ...JSON.parse(String(init.body)) }),
    })
    render(<LlmSettingsPanel />)
    await screen.findByText('••••abcd')

    await userEvent.click(screen.getByRole('button', { name: 'Заменить ключ OpenAI' }))
    await userEvent.type(screen.getByLabelText('Ключ OpenAI'), 'sk-new-key-9999')
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(callsTo(fetchMock, 'PUT', '/api/settings/llm')).toHaveLength(1))
    expect(lastPutBody(fetchMock).openai_key).toBe('sk-new-key-9999')
  })

  it('clears the key when the user removes it', async () => {
    const fetchMock = mockApi({
      'GET /api/settings/llm': llm,
      'PUT /api/settings/llm': (init: RequestInit) => ({ ...llm, ...JSON.parse(String(init.body)) }),
    })
    render(<LlmSettingsPanel />)
    await screen.findByText('••••abcd')
    await userEvent.click(screen.getByRole('button', { name: 'Удалить ключ OpenAI' }))
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(callsTo(fetchMock, 'PUT', '/api/settings/llm')).toHaveLength(1))
    expect(lastPutBody(fetchMock).openai_key).toBeNull()
  })
})

describe('Anthropic (Claude) settings', () => {
  it('offers Claude as primary and emergency provider and saves model, effort and masked key', async () => {
    const fetchMock = mockApi({
      'GET /api/settings/llm': llm,
      'PUT /api/settings/llm': (init: RequestInit) => ({ ...llm, ...JSON.parse(String(init.body)) }),
    })
    render(<LlmSettingsPanel />)
    const model = await screen.findByLabelText('Модель Claude')
    expect(model).toHaveValue('claude-sonnet-5-5')
    expect(screen.getByText('••••9876')).toBeInTheDocument()
    // only Sonnet and Haiku are offered for the bot
    expect([...(model as HTMLSelectElement).options].map((o) => o.value)).toEqual(['claude-sonnet-5-5', 'claude-haiku-5-5'])

    await userEvent.selectOptions(screen.getByLabelText('Основной провайдер'), 'Anthropic (Claude)')
    await userEvent.selectOptions(screen.getByLabelText('Аварийный провайдер'), 'OpenAI')
    await userEvent.selectOptions(screen.getByLabelText('Глубина размышлений'), 'Высокая')
    await userEvent.selectOptions(model, 'claude-haiku-5-5')
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }))

    await waitFor(() => expect(callsTo(fetchMock, 'PUT', '/api/settings/llm')).toHaveLength(1))
    const body = lastPutBody(fetchMock)
    expect(body).toMatchObject({
      provider: 'anthropic',
      emergency_provider: 'openai',
      anthropic_model: 'claude-haiku-5-5',
      anthropic_effort: 'high',
      anthropic_key: '••••9876', // masked = unchanged
    })
  })

  it('replaces the Anthropic key', async () => {
    const fetchMock = mockApi({
      'GET /api/settings/llm': llm,
      'PUT /api/settings/llm': (init: RequestInit) => ({ ...llm, ...JSON.parse(String(init.body)) }),
    })
    render(<LlmSettingsPanel />)
    await screen.findByText('••••9876')
    await userEvent.click(screen.getByRole('button', { name: 'Заменить ключ Anthropic' }))
    await userEvent.type(screen.getByLabelText('Ключ Anthropic'), 'sk-ant-new-1')
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(callsTo(fetchMock, 'PUT', '/api/settings/llm')).toHaveLength(1))
    expect(lastPutBody(fetchMock).anthropic_key).toBe('sk-ant-new-1')
  })
})

describe('model pickers', () => {
  it('loads the OpenAI model list for the key and saves the chosen model', async () => {
    const fetchMock = mockApi({
      'GET /api/settings/llm': llm,
      'POST /api/settings/llm/models': { ok: true, models: ['gpt-5.5', 'gpt-5.4-mini'] },
      'PUT /api/settings/llm': (init: RequestInit) => ({ ...llm, ...JSON.parse(String(init.body)) }),
    })
    render(<LlmSettingsPanel />)
    const model = await screen.findByLabelText('Основная модель OpenAI')
    expect(model).toHaveValue('gpt-4.1-mini') // the stored value stays selectable
    await userEvent.click(screen.getAllByRole('button', { name: 'Загрузить список моделей' })[0])
    await waitFor(() => expect(screen.getByRole('option', { name: 'gpt-5.5' })).toBeInTheDocument())
    const listed = JSON.parse(String((callsTo(fetchMock, 'POST', '/api/settings/llm/models')[0][1] as RequestInit).body))
    expect(listed).toMatchObject({ provider: 'openai', openai_key: '••••abcd' })
    await userEvent.selectOptions(model, 'gpt-5.5')
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(callsTo(fetchMock, 'PUT', '/api/settings/llm')).toHaveLength(1))
    expect(lastPutBody(fetchMock)).toMatchObject({ openai_model: 'gpt-5.5' })
  })

  it('allows typing a model that is not in the list', async () => {
    mockApi({ 'GET /api/settings/llm': llm })
    render(<LlmSettingsPanel />)
    const gemini = await screen.findByLabelText('Модель Gemini')
    await userEvent.selectOptions(gemini, 'Другая модель…')
    const input = screen.getByLabelText('Модель Gemini')
    expect(input.tagName).toBe('INPUT')
    await userEvent.clear(input)
    await userEvent.type(input, 'gemini-custom')
    expect(input).toHaveValue('gemini-custom')
  })
})
