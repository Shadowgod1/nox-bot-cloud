import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { Conversations } from './Conversations'
import { FakeEventSource, mockApi } from '../../test/utils'
import { __resetEventsForTests } from '../../hooks/useEvents'

const conv = (id: number, name: string, last: string) => ({
  id,
  chat_jid: `7701000000${id}@s.whatsapp.net`,
  phone: `+7701000000${id}`,
  name,
  status: 'active',
  paused_until: null,
  last_message_at: '2026-11-11T10:00:00Z',
  unread: 0,
  last_text: last,
})

describe('Conversations', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    __resetEventsForTests()
    vi.stubGlobal('EventSource', FakeEventSource)
    mockApi({
      'GET /api/conversations': { items: [conv(1, 'Данияр', 'Привет'), conv(2, 'Алина', 'Ок')], next_cursor: null },
      'GET /api/conversations/1/messages': {
        items: [
          { id: 10, conversation_id: 1, direction: 'in', author: 'client', text: 'Привет', created_at: '2026-11-11T10:00:00Z' },
        ],
        next_cursor: null,
      },
    })
  })

  it('appends message on SSE message.new', async () => {
    render(
      <MemoryRouter initialEntries={['/conversations/1']}>
        <Routes>
          <Route path="/conversations/:id?" element={<Conversations />} />
        </Routes>
      </MemoryRouter>,
    )
    const log = await screen.findByRole('log')
    await within(log).findByText('Привет')
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(FakeEventSource.instances[0].url).toBe('/api/events')

    act(() => {
      FakeEventSource.instances[0].emit('message.new', {
        id: 11,
        conversation_id: 1,
        direction: 'in',
        author: 'client',
        text: 'Есть места на вечер?',
        created_at: '2026-11-11T10:01:00Z',
      })
    })
    expect(within(log).getByText('Есть места на вечер?')).toBeInTheDocument()

    // A message for another conversation does not land in the open chat, but updates the list.
    act(() => {
      FakeEventSource.instances[0].emit('message.new', {
        conversation_id: 2,
        message: { id: 12, conversation_id: 2, direction: 'in', author: 'client', text: 'Чужое', created_at: '2026-11-11T10:02:00Z' },
      })
    })
    expect(within(log).queryByText('Чужое')).not.toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Список диалогов' })).toHaveTextContent('Чужое')

    // Duplicate delivery is ignored.
    act(() => {
      FakeEventSource.instances[0].emit('message.new', {
        id: 11, conversation_id: 1, direction: 'in', author: 'client', text: 'Есть места на вечер?', created_at: '2026-11-11T10:01:00Z',
      })
    })
    expect(within(log).getAllByText('Есть места на вечер?')).toHaveLength(1)
  })
})
