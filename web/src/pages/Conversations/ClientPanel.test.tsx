import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { Conversations } from './Conversations'
import { FORGET_CONFIRM } from './ClientPanel'
import { FakeEventSource, callsTo, mockApi } from '../../test/utils'
import { __resetEventsForTests } from '../../hooks/useEvents'
import type { ClientCard } from '../../api/types'

const conv = {
  id: 2,
  chat_jid: '77010000002@s.whatsapp.net',
  phone: '77010000002',
  name: 'Алина',
  status: 'active',
  paused_until: null,
  last_message_at: '2026-11-11T10:00:00Z',
  unread: 0,
  last_message: null,
}

// Shape as returned by core/iler/api/client_card.py.
const CARD: ClientCard = {
  name: 'Аружан',
  notes: ['Ходит к мастеру Айгерим', 'Аллергия на латекс'],
  bookings: [{ id: 12, service: 'Маникюр', when: '28.11.2026 15:00', status: 'pending', created_at: '2026-11-26T10:00:00Z' }],
  updated_at: '2026-11-26T10:00:00Z',
}

function renderChat() {
  let card: ClientCard = structuredClone(CARD)
  const fetchMock = mockApi({
    'GET /api/conversations': { items: [conv], next_cursor: null },
    'GET /api/conversations/2/messages': { items: [], next_cursor: null },
    'GET /api/conversations/2/client': () => card,
    'POST /api/conversations/2/client/notes/remove': (init: RequestInit) => {
      const { fact } = JSON.parse(String(init.body)) as { fact: string }
      card = { ...card, notes: card.notes.filter((n) => n !== fact) }
      return card
    },
    'DELETE /api/conversations/2/client': () => {
      card = { ...card, name: '', notes: [] }
      return card
    },
  })
  render(
    <MemoryRouter initialEntries={['/conversations/2']}>
      <Routes>
        <Route path="/conversations/:id?" element={<Conversations />} />
      </Routes>
    </MemoryRouter>,
  )
  return fetchMock
}

describe('Что бот знает о клиенте', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    __resetEventsForTests()
    vi.stubGlobal('EventSource', FakeEventSource)
  })
  afterEach(() => vi.restoreAllMocks())

  it('loads only when opened and shows the name, notes and bookings', async () => {
    const user = userEvent.setup()
    const fetchMock = renderChat()
    const toggle = await screen.findByRole('button', { name: 'Что бот знает о клиенте' })
    expect(callsTo(fetchMock, 'GET', '/api/conversations/2/client')).toHaveLength(0)
    await user.click(toggle)
    const panel = await screen.findByRole('complementary', { name: 'Что бот знает о клиенте' })
    expect(await within(panel).findByText('Аружан')).toBeInTheDocument()
    expect(within(panel).getByText('Ходит к мастеру Айгерим')).toBeInTheDocument()
    expect(within(panel).getByText(/Маникюр, 28.11.2026 15:00/)).toBeInTheDocument()
    expect(within(panel).getByText('Ожидает')).toBeInTheDocument()
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
    await user.click(within(panel).getByRole('button', { name: 'Закрыть панель' }))
    expect(screen.queryByRole('complementary', { name: 'Что бот знает о клиенте' })).not.toBeInTheDocument()
  })

  it('deletes one note', async () => {
    const user = userEvent.setup()
    const fetchMock = renderChat()
    await user.click(await screen.findByRole('button', { name: 'Что бот знает о клиенте' }))
    const panel = await screen.findByRole('complementary', { name: 'Что бот знает о клиенте' })
    await user.click(await within(panel).findByRole('button', { name: 'Удалить заметку «Аллергия на латекс»' }))
    await waitFor(() => expect(within(panel).queryByText('Аллергия на латекс')).not.toBeInTheDocument())
    expect(within(panel).getByText('Ходит к мастеру Айгерим')).toBeInTheDocument()
    const [, init] = callsTo(fetchMock, 'POST', '/api/conversations/2/client/notes/remove')[0]
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({ fact: 'Аллергия на латекс' })
  })

  it('forgets the client after confirmation; bookings stay', async () => {
    const user = userEvent.setup()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    const fetchMock = renderChat()
    await user.click(await screen.findByRole('button', { name: 'Что бот знает о клиенте' }))
    const panel = await screen.findByRole('complementary', { name: 'Что бот знает о клиенте' })
    const forget = await within(panel).findByRole('button', { name: 'Забыть клиента' })
    await user.click(forget)
    expect(confirm).toHaveBeenCalledWith(FORGET_CONFIRM)
    expect(callsTo(fetchMock, 'DELETE', '/api/conversations/2/client')).toHaveLength(0)
    await user.click(forget)
    expect(await within(panel).findByText('Клиент не называл имя')).toBeInTheDocument()
    expect(within(panel).getByText(/Заметок пока нет/)).toBeInTheDocument()
    expect(within(panel).getByText(/Маникюр, 28.11.2026 15:00/)).toBeInTheDocument()
    expect(within(panel).getByRole('button', { name: 'Забыть клиента' })).toBeDisabled()
  })
})
