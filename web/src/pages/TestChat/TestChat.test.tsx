import { describe, it, expect } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { TestChat } from './TestChat'
import { callsTo, mockApi } from '../../test/utils'
import { BOT } from '../../test/fixtures'
import type { BotSettings } from '../../api/types'

const SALON: BotSettings = { ...BOT, template_id: 'beauty_salon', prompt: 'Ты — администратор салона.' }

function renderChat(reply: (body: { messages: { role: string; content: string; media?: string }[] }) => unknown, bot = SALON) {
  const fetchMock = mockApi({
    'GET /api/settings/bot': bot,
    'POST /api/test-chat': (init: RequestInit) => reply(JSON.parse(String(init.body))),
  })
  render(
    <MemoryRouter>
      <TestChat />
    </MemoryRouter>,
  )
  return fetchMock
}

describe('Тестовый чат', () => {
  it('offers salon examples, shows the reply with its coins and starts over', async () => {
    const user = userEvent.setup()
    const fetchMock = renderChat(() => ({ reply: 'Маникюр — от 6 000 ₸.', tool_calls: [], usage: { coins: 0.42, input_tokens: 3000, output_tokens: 80 } }))
    const examples = await screen.findByLabelText('Примеры вопросов')
    for (const q of ['Сколько стоит маникюр?', 'Можно к Айгерим в четверг после 6?', 'Где вы находитесь?']) {
      expect(within(examples).getByRole('button', { name: q })).toBeInTheDocument()
    }
    await user.click(within(examples).getByRole('button', { name: 'Сколько стоит маникюр?' }))
    expect(await screen.findByText('Маникюр — от 6 000 ₸.')).toBeInTheDocument()
    expect(screen.getByText('0,42 коина')).toBeInTheDocument()
    expect(screen.getByTestId('test-chat-spent')).toHaveTextContent('Потрачено в тесте: 0,42 коина')
    const body = JSON.parse(String((callsTo(fetchMock, 'POST', '/api/test-chat')[0][1] as RequestInit).body))
    expect(body).toEqual({ messages: [{ role: 'user', content: 'Сколько стоит маникюр?' }] })

    await user.click(screen.getByRole('button', { name: 'Начать заново' }))
    expect(screen.queryByText('Маникюр — от 6 000 ₸.')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Примеры вопросов')).toBeInTheDocument()
  })

  it('simulates a voice message and shows that WhatsApp would hand over', async () => {
    const user = userEvent.setup()
    const fetchMock = renderChat(() => ({ reply: 'Спасибо! Передам ваше голосовое администратору.', tool_calls: [], handed_over: true, usage: null }))
    await user.click(await screen.findByRole('button', { name: 'голосовое' }))
    expect(await screen.findByText('Спасибо! Передам ваше голосовое администратору.')).toBeInTheDocument()
    expect(screen.getByText('Голосовое сообщение')).toBeInTheDocument()
    expect(screen.getByText(/диалог перешёл бы к администратору/)).toBeInTheDocument()
    const body = JSON.parse(String((callsTo(fetchMock, 'POST', '/api/test-chat')[0][1] as RequestInit).body))
    expect(body.messages[0]).toEqual({ role: 'user', content: '', media: 'audio' })
  })

  it('sends a photo with the typed caption', async () => {
    const user = userEvent.setup()
    const fetchMock = renderChat(() => ({ reply: 'Спасибо! Передам фото мастеру.', tool_calls: [], handed_over: true, usage: null }))
    await user.type(await screen.findByLabelText('Сообщение боту'), 'хочу такой дизайн')
    await user.click(screen.getByRole('button', { name: 'фото с подписью' }))
    await screen.findByText('Спасибо! Передам фото мастеру.')
    const body = JSON.parse(String((callsTo(fetchMock, 'POST', '/api/test-chat')[0][1] as RequestInit).body))
    expect(body.messages[0]).toEqual({ role: 'user', content: 'хочу такой дизайн', media: 'image' })
  })

  it('asks to set the bot up first when the prompt is empty', async () => {
    renderChat(() => ({}), { ...BOT, prompt: '' })
    expect(await screen.findByText('Сначала настройте бота')).toBeInTheDocument()
    expect(screen.queryByLabelText('Примеры вопросов')).not.toBeInTheDocument()
  })
})
