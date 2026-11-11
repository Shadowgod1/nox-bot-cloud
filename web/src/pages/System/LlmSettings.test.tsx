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

    await userEvent.clear(model)
    await userEvent.type(model, 'gpt-4.1')
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }))

    await waitFor(() => expect(callsTo(fetchMock, 'PUT', '/api/settings/llm')).toHaveLength(1))
    const body = lastPutBody(fetchMock)
    expect(body.openai_key).toBe('••••abcd')
    expect(body.openai_model).toBe('gpt-4.1')
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
