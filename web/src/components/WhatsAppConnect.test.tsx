import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { WhatsAppConnect } from './WhatsAppConnect'
import { callsTo, mockApi } from '../test/utils'

describe('WhatsAppConnect', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('polls status every 4s and stops on open', async () => {
    vi.useFakeTimers()
    const states = ['close', 'connecting', 'connecting', 'open']
    let i = 0
    const fetchMock = mockApi({
      'GET /api/whatsapp/status': () => ({
        status: states[Math.min(i++, states.length - 1)],
        phone: i >= states.length ? '+77010000000' : null,
        last_error: null,
      }),
    })
    const onConnected = vi.fn()

    render(<WhatsAppConnect onConnected={onConnected} />)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(callsTo(fetchMock, 'GET', '/api/whatsapp/status')).toHaveLength(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3999)
    })
    expect(callsTo(fetchMock, 'GET', '/api/whatsapp/status')).toHaveLength(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(callsTo(fetchMock, 'GET', '/api/whatsapp/status')).toHaveLength(2)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000)
    })
    expect(callsTo(fetchMock, 'GET', '/api/whatsapp/status')).toHaveLength(4)
    expect(onConnected).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/WhatsApp подключён/i)).toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(40_000)
    })
    expect(callsTo(fetchMock, 'GET', '/api/whatsapp/status')).toHaveLength(4)
  })
})
