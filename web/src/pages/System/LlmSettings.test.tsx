import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { LlmSettingsPanel } from './LlmSettings'
import { callsTo, mockApi } from '../../test/utils'

const llm = {
  provider: 'openai',
  openai_model: 'gpt-4.1-mini',
  openai_fallback_model: null,
  gemini_model: 'gemini-2.5-flash',
  anthropic_model: 'claude-sonnet-5-5',
  anthropic_effort: 'low',
  emergency_provider: null,
}

const ALL = { providers: ['anthropic', 'openai', 'gemini'] }

function lastPutBody(fetchMock: ReturnType<typeof mockApi>) {
  const calls = callsTo(fetchMock, 'PUT', '/api/settings/llm')
  const init = calls[calls.length - 1][1] as RequestInit
  return JSON.parse(String(init.body))
}

function optionValues(el: HTMLElement) {
  return [...(el as HTMLSelectElement).options].map((o) => o.value)
}

describe('LLM settings with ILER keys', () => {
  it('shows only the providers ILER has keys for and no key fields', async () => {
    mockApi({
      'GET /api/settings/llm': llm,
      'GET /api/settings/llm/providers': { providers: ['anthropic', 'openai'] },
      'POST /api/settings/llm/models': { ok: true, models: ['gpt-5.5', 'gpt-5.4-mini'] },
    })
    render(<LlmSettingsPanel />)
    expect(await screen.findByLabelText('Основная модель OpenAI')).toBeInTheDocument()
    expect(screen.getByLabelText('Модель Claude')).toBeInTheDocument()
    expect(screen.queryByLabelText('Модель Gemini')).not.toBeInTheDocument()
    expect(optionValues(screen.getByLabelText('Основной провайдер'))).toEqual(['anthropic', 'openai'])
    // the main provider (openai) is not offered as its own fallback
    expect(optionValues(screen.getByLabelText('Аварийный провайдер'))).toEqual(['', 'anthropic'])
    expect(screen.queryByLabelText(/Ключ/)).toBeNull()
    expect(screen.getByText(/Ключи доступа к провайдерам предоставляет ILER/)).toBeInTheDocument()
  })

  it('never offers the main provider as the fallback; with one provider the fallback is off', async () => {
    mockApi({
      'GET /api/settings/llm': { ...llm, provider: 'anthropic', emergency_provider: 'openai' },
      'GET /api/settings/llm/providers': { providers: ['anthropic', 'openai'] },
      'POST /api/settings/llm/models': { ok: true, models: [] },
    })
    render(<LlmSettingsPanel />)
    const fallback = await screen.findByLabelText('Аварийный провайдер')
    expect(fallback).toHaveValue('openai')
    // the main provider becomes the former fallback: the fallback resets and offers the other one
    await userEvent.selectOptions(screen.getByLabelText('Основной провайдер'), 'OpenAI')
    expect(fallback).toHaveValue('')
    expect(optionValues(fallback)).toEqual(['', 'anthropic'])
  })

  it('disables the fallback with a note when there is no other provider', async () => {
    mockApi({
      'GET /api/settings/llm': { ...llm, provider: 'anthropic', emergency_provider: 'anthropic' },
      'GET /api/settings/llm/providers': { providers: ['anthropic'] },
    })
    render(<LlmSettingsPanel />)
    const fallback = await screen.findByLabelText('Аварийный провайдер')
    expect(fallback).toBeDisabled()
    expect(fallback).toHaveValue('')
    expect(optionValues(fallback)).toEqual([''])
    expect(screen.getByText('Других провайдеров сейчас нет, подменить основной некем.')).toBeInTheDocument()
  })

  it('shows the support message when no ILER keys are configured', async () => {
    mockApi({ 'GET /api/settings/llm': llm, 'GET /api/settings/llm/providers': { providers: [] } })
    render(<LlmSettingsPanel />)
    expect(await screen.findByText(/Ключи ILER не настроены\. Обратитесь в поддержку ILER/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Основной провайдер')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Сохранить' })).not.toBeInTheDocument()
  })

  it('saves the chosen model without any key fields in the body', async () => {
    const fetchMock = mockApi({
      'GET /api/settings/llm': llm,
      'GET /api/settings/llm/providers': ALL,
      'POST /api/settings/llm/models': { ok: true, models: ['gpt-5.4-mini', 'gpt-5.4-nano'] },
      'PUT /api/settings/llm': (init: RequestInit) => ({ ...llm, ...JSON.parse(String(init.body)) }),
    })
    render(<LlmSettingsPanel />)
    const model = await screen.findByLabelText('Основная модель OpenAI')
    await waitFor(() => expect(screen.getAllByRole('option', { name: 'gpt-5.4-nano (рекомендуется)' }).length).toBeGreaterThan(0))
    await userEvent.selectOptions(model, 'gpt-5.4-nano')
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }))

    await waitFor(() => expect(callsTo(fetchMock, 'PUT', '/api/settings/llm')).toHaveLength(1))
    const body = lastPutBody(fetchMock)
    expect(body.openai_model).toBe('gpt-5.4-nano')
    expect(Object.keys(body).filter((k) => k.endsWith('_key'))).toEqual([])
  })
})

describe('key check', () => {
  it('clears the check result when the primary provider changes', async () => {
    mockApi({
      'GET /api/settings/llm': llm,
      'GET /api/settings/llm/providers': ALL,
      'POST /api/settings/llm/models': { ok: true, models: [] },
      'POST /api/settings/llm/test': { ok: false, error: 'openai: Провайдер модели отклонил ключ доступа. Обратитесь к поставщику бота.' },
    })
    render(<LlmSettingsPanel />)
    await screen.findByLabelText('Основной провайдер')
    await userEvent.click(screen.getByRole('button', { name: 'Проверить' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Не работает: openai: Провайдер модели отклонил ключ доступа')
    await userEvent.selectOptions(screen.getByLabelText('Основной провайдер'), 'Anthropic (Claude)')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})

describe('Anthropic (Claude) settings', () => {
  it('offers Claude as primary and emergency provider and saves model and effort', async () => {
    const fetchMock = mockApi({
      'GET /api/settings/llm': llm,
      'GET /api/settings/llm/providers': ALL,
      'POST /api/settings/llm/models': { ok: true, models: [] },
      'PUT /api/settings/llm': (init: RequestInit) => ({ ...llm, ...JSON.parse(String(init.body)) }),
    })
    render(<LlmSettingsPanel />)
    const model = await screen.findByLabelText('Модель Claude')
    expect(model).toHaveValue('claude-sonnet-5-5')
    // only Sonnet and Haiku are offered for the bot
    expect(optionValues(model)).toEqual(['claude-sonnet-5-5', 'claude-haiku-5-5'])

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
    })
    expect(body).not.toHaveProperty('anthropic_key')
  })
})

describe('model pickers', () => {
  it('loads model lists for available OpenAI / Gemini providers on mount', async () => {
    const fetchMock = mockApi({
      'GET /api/settings/llm': llm,
      'GET /api/settings/llm/providers': ALL,
      'POST /api/settings/llm/models': (init: RequestInit) =>
        JSON.parse(String(init.body)).provider === 'openai'
          ? { ok: true, models: ['gpt-5.5', 'gpt-5.4-mini'] }
          : { ok: true, models: ['gemini-3.8-flash'] },
      'PUT /api/settings/llm': (init: RequestInit) => ({ ...llm, ...JSON.parse(String(init.body)) }),
    })
    render(<LlmSettingsPanel />)
    const model = await screen.findByLabelText('Основная модель OpenAI')
    expect(model).toHaveValue('gpt-4.1-mini') // the stored value stays selectable
    await waitFor(() => expect(screen.getByRole('option', { name: 'gpt-5.5' })).toBeInTheDocument())
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/settings/llm/models')).toHaveLength(2))
    const listed = callsTo(fetchMock, 'POST', '/api/settings/llm/models').map(
      ([, init]) => JSON.parse(String((init as RequestInit).body)).provider,
    )
    expect(listed).toEqual(['openai', 'gemini'])
    expect(screen.getAllByRole('button', { name: 'Обновить список моделей' })).toHaveLength(2)

    await userEvent.selectOptions(model, 'gpt-5.5')
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(callsTo(fetchMock, 'PUT', '/api/settings/llm')).toHaveLength(1))
    expect(lastPutBody(fetchMock)).toMatchObject({ openai_model: 'gpt-5.5' })
  })

  it('allows typing a model that is not in the list', async () => {
    mockApi({
      'GET /api/settings/llm': llm,
      'GET /api/settings/llm/providers': ALL,
      'POST /api/settings/llm/models': { ok: false, models: [], error: 'unavailable' },
    })
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
