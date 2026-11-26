import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { Conversations } from './Conversations'
import { FakeEventSource, callsTo, jsonResponse, mockApi } from '../../test/utils'
import { __resetEventsForTests } from '../../hooks/useEvents'

// Shapes as returned by core/iler/api/conversations.py.
const conv = (id: number, name: string, last: string, at = '2026-11-11T10:00:00Z') => ({
  id,
  chat_jid: `7701000000${id}@s.whatsapp.net`,
  phone: `7701000000${id}`,
  name,
  status: 'active',
  paused_until: null,
  last_message_at: at,
  unread: 0,
  last_message: { text: last, author: 'client', direction: 'in', created_at: at },
})

const msg = (id: number, conversation_id: number, text: string, at: string) => ({
  id,
  conversation_id,
  direction: 'in',
  author: 'client',
  text,
  media: null,
  created_at: at,
})

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/conversations/:id?" element={<Conversations />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Conversations', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    __resetEventsForTests()
    vi.stubGlobal('EventSource', FakeEventSource)
  })

  it('appends message on SSE message.new', async () => {
    mockApi({
      'GET /api/conversations': { items: [conv(1, 'Данияр', 'Привет'), conv(2, 'Алина', 'Ок')], next_cursor: null },
      'GET /api/conversations/1/messages': { items: [msg(10, 1, 'Привет', '2026-11-11T10:00:00Z')], next_cursor: null },
    })
    renderAt('/conversations/1')
    const log = await screen.findByRole('log')
    await within(log).findByText('Привет')
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(FakeEventSource.instances[0].url).toBe('/api/events')

    act(() => {
      FakeEventSource.instances[0].emit('message.new', {
        conversation_id: 1,
        message: { ...msg(11, 1, 'Есть места на вечер?', '2026-11-11T10:01:00+00:00'), evo_message_id: 'ABC' },
      })
    })
    expect(within(log).getByText('Есть места на вечер?')).toBeInTheDocument()

    // A message for another conversation does not land in the open chat, but updates the list.
    act(() => {
      FakeEventSource.instances[0].emit('message.new', {
        conversation_id: 2,
        message: msg(12, 2, 'Чужое', '2026-11-11T10:02:00+00:00'),
      })
    })
    expect(within(log).queryByText('Чужое')).not.toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: 'Список диалогов' })
    expect(nav).toHaveTextContent('Чужое')
    // ...and moves it to the top (timestamps mix "Z" and "+00:00").
    const links = within(nav).getAllByRole('link')
    expect(links[0]).toHaveTextContent('Алина')

    // Duplicate delivery is ignored.
    act(() => {
      FakeEventSource.instances[0].emit('message.new', {
        conversation_id: 1,
        message: msg(11, 1, 'Есть места на вечер?', '2026-11-11T10:01:00+00:00'),
      })
    })
    expect(within(log).getAllByText('Есть места на вечер?')).toHaveLength(1)
  })

  it('applies both conversation.updated shapes (engine row and API conversation)', async () => {
    mockApi({
      'GET /api/conversations': { items: [conv(1, 'Данияр', 'Привет'), conv(2, 'Алина', 'Ок')], next_cursor: null },
    })
    renderAt('/conversations')
    const nav = await screen.findByRole('navigation', { name: 'Список диалогов' })
    await within(nav).findByText('Данияр')

    // Engine shape: {conversation: row} without last_message — the preview must survive.
    const { last_message: _lm, ...row } = conv(2, 'Алина', 'Ок')
    act(() => {
      FakeEventSource.instances[0].emit('conversation.updated', {
        conversation: { ...row, status: 'needs_operator', unread: 3, created_at: '2026-11-01T00:00:00+00:00' },
      })
    })
    expect(within(nav).getByText('Нужен оператор')).toBeInTheDocument()
    expect(within(nav).getByLabelText('Непрочитанных: 3')).toBeInTheDocument()
    expect(within(nav).getByText('Ок')).toBeInTheDocument()

    // API shape: flat conversation (e.g. after release).
    act(() => {
      FakeEventSource.instances[0].emit('conversation.updated', { ...conv(2, 'Алина', 'Ок'), status: 'active', unread: 0 })
    })
    expect(within(nav).queryByText('Нужен оператор')).not.toBeInTheDocument()
  })

  it('loads the next conversations page with the opaque cursor', async () => {
    const fetchMock = mockApi({
      'GET /api/conversations': (_init: RequestInit, url: string) => {
        const cursor = new URL(url, 'http://x').searchParams.get('cursor')
        if (!cursor) {
          return { items: [conv(3, 'Первый', 'a', '2026-11-11T10:03:00Z'), conv(2, 'Второй', 'b', '2026-11-11T10:02:00Z')], next_cursor: '1762855320000000_2' }
        }
        expect(cursor).toBe('1762855320000000_2')
        return { items: [conv(2, 'Второй', 'b', '2026-11-11T10:02:00Z'), conv(1, 'Третий', 'c', '2026-11-11T10:01:00Z')], next_cursor: null }
      },
    })
    renderAt('/conversations')
    const nav = await screen.findByRole('navigation', { name: 'Список диалогов' })
    await within(nav).findByText('Первый')
    expect(within(nav).queryByText('Третий')).not.toBeInTheDocument()

    await userEvent.click(within(nav).getByRole('button', { name: 'Показать ещё' }))
    await within(nav).findByText('Третий')

    const calls = callsTo(fetchMock, 'GET', '/api/conversations')
    expect(calls).toHaveLength(2)
    expect(String(calls[1][0])).toContain('cursor=1762855320000000_2')
    // Overlapping item is not duplicated; no more pages -> button gone.
    expect(within(nav).getAllByText('Второй')).toHaveLength(1)
    await waitFor(() => expect(within(nav).queryByRole('button', { name: 'Показать ещё' })).not.toBeInTheDocument())
  })

  it('shows messages oldest first and loads older ones with the cursor', async () => {
    const fetchMock = mockApi({
      'GET /api/conversations': { items: [conv(1, 'Данияр', 'Третье')], next_cursor: null },
      'GET /api/conversations/1/messages': (_init: RequestInit, url: string) => {
        const cursor = new URL(url, 'http://x').searchParams.get('cursor')
        // Newest first, like core.
        return cursor === '20'
          ? { items: [msg(19, 1, 'Первое', '2026-11-11T09:58:00Z')], next_cursor: null }
          : { items: [msg(21, 1, 'Третье', '2026-11-11T10:00:00Z'), msg(20, 1, 'Второе', '2026-11-11T09:59:00Z')], next_cursor: '20' }
      },
    })
    renderAt('/conversations/1')
    const log = await screen.findByRole('log')
    await within(log).findByText('Второе')
    const texts = () => Array.from(log.querySelectorAll('[title]')).map((el) => el.textContent ?? '')
    expect(texts()[0]).toContain('Второе')

    await userEvent.click(within(log).getByRole('button', { name: 'Показать более ранние' }))
    await within(log).findByText('Первое')
    expect(texts()[0]).toContain('Первое')
    expect(texts()[2]).toContain('Третье')
    expect(String(callsTo(fetchMock, 'GET', '/api/conversations/1/messages')[1][0])).toContain('cursor=20')
  })

  it('keeps the request_id when a failed send is retried and renews it otherwise', async () => {
    const results = [503, 200, 200, 200]
    const fetchMock = mockApi({
      'GET /api/conversations': { items: [conv(1, 'Данияр', 'Привет')], next_cursor: null },
      'GET /api/conversations/1/messages': { items: [], next_cursor: null },
      'POST /api/conversations/1/send': () => {
        const status = results.shift() ?? 200
        return status === 200 ? { ok: true } : jsonResponse({ error: { code: 'send_failed', message: 'нет' } }, status)
      },
    })
    renderAt('/conversations/1')
    const box = await screen.findByLabelText('Ответ клиенту')
    const submit = screen.getByRole('button', { name: 'Отправить' })
    const sends = () => callsTo(fetchMock, 'POST', '/api/conversations/1/send')
    const sent = (i: number) => JSON.parse(String((sends()[i][1] as RequestInit).body)) as { text: string; request_id: string }

    await userEvent.type(box, 'Здравствуйте')
    await userEvent.click(submit) // fails: the draft stays
    await waitFor(() => expect(sends()).toHaveLength(1))
    await waitFor(() => expect(submit).not.toBeDisabled())
    expect(box).toHaveValue('Здравствуйте')
    await userEvent.click(submit) // retry of the same text: same id
    await waitFor(() => expect(box).toHaveValue(''))
    expect(sent(1).request_id).toBe(sent(0).request_id)
    expect(sent(0).request_id).toMatch(/^[A-Za-z0-9_-]{8,64}$/)

    await userEvent.type(box, 'Здравствуйте') // after a success the same text is a new message
    await userEvent.click(submit)
    await waitFor(() => expect(sends()).toHaveLength(3))
    await waitFor(() => expect(box).toHaveValue(''))
    expect(sent(2).request_id).not.toBe(sent(1).request_id)
    expect(sent(2).request_id).toMatch(/^[A-Za-z0-9_-]{8,64}$/)
  })

  it('gives a new request_id when the text of a failed send is changed', async () => {
    const fetchMock = mockApi({
      'GET /api/conversations': { items: [conv(1, 'Данияр', 'Привет')], next_cursor: null },
      'GET /api/conversations/1/messages': { items: [], next_cursor: null },
      'POST /api/conversations/1/send': () => jsonResponse({ error: { code: 'send_failed', message: 'нет' } }, 503),
    })
    renderAt('/conversations/1')
    const box = await screen.findByLabelText('Ответ клиенту')
    const submit = screen.getByRole('button', { name: 'Отправить' })
    const sends = () => callsTo(fetchMock, 'POST', '/api/conversations/1/send')
    const sent = (i: number) => JSON.parse(String((sends()[i][1] as RequestInit).body)) as { text: string; request_id: string }

    await userEvent.type(box, 'Здравствуйте')
    await userEvent.click(submit)
    await waitFor(() => expect(sends()).toHaveLength(1))
    await waitFor(() => expect(submit).not.toBeDisabled())
    await userEvent.type(box, '!')
    await userEvent.click(submit)
    await waitFor(() => expect(sends()).toHaveLength(2))
    expect(sent(1).text).toBe('Здравствуйте!')
    expect(sent(1).request_id).not.toBe(sent(0).request_id)
    expect(sent(1).request_id).toMatch(/^[A-Za-z0-9_-]{8,64}$/)
  })
})
