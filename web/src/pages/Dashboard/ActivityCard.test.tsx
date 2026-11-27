import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ActivityCard } from './ActivityCard'
import { callsTo, mockApi } from '../../test/utils'
import type { Activity } from '../../api/types'

const zero = { conversations: 0, client_messages: 0, bot_replies: 0, bookings: 0, handovers: 0 }

function activity(days: number, over: Partial<Activity> = {}): Activity {
  const series = Array.from({ length: days }, (_, i) => ({
    date: `2026-11-${String(i + 1).padStart(2, '0')}`,
    ...zero,
  }))
  return { days, since: series[0].date, series, hours: Array(24).fill(0), totals: zero, previous: zero, median_reply_seconds: null, ...over }
}

describe('ActivityCard', () => {
  it('shows totals with the trend, median reply time and the client peak hour', async () => {
    const hours = Array(24).fill(0)
    hours[19] = 10
    hours[23] = 5
    mockApi({
      'GET /api/dashboard/activity': () =>
        activity(14, {
          totals: { conversations: 120, client_messages: 300, bot_replies: 280, bookings: 30, handovers: 12 },
          previous: { conversations: 100, client_messages: 250, bot_replies: 240, bookings: 30, handovers: 10 },
          median_reply_seconds: 24,
          hours,
        }),
    })
    render(<ActivityCard />)
    expect(await screen.findByText('120')).toBeInTheDocument()
    expect(screen.getByText('+20%')).toBeInTheDocument()
    expect(screen.getByText('0%')).toBeInTheDocument() // bookings unchanged
    expect(screen.getByText('10%')).toBeInTheDocument() // 12 of 120 handed over
    expect(screen.getByText('24 с')).toBeInTheDocument()
    expect(screen.getByText('19:00–20:00')).toBeInTheDocument()
    expect(screen.getByText(/в нерабочее время \(21:00–9:00\) — 33% сообщений/)).toBeInTheDocument()
  })

  it('empty bot: friendly empty state; the period switch reloads', async () => {
    const user = userEvent.setup()
    const fetchMock = mockApi({
      'GET /api/dashboard/activity': (_init: RequestInit, url: string) => activity(Number(new URL(url, 'http://x').searchParams.get('days'))),
    })
    render(<ActivityCard />)
    expect(await screen.findByText('Графики заполнятся, когда клиенты начнут писать в WhatsApp.')).toBeInTheDocument()
    expect(screen.getByText('Покажем, в какие часы клиенты пишут чаще всего.')).toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: '30 дней' }))
    await waitFor(() =>
      expect(callsTo(fetchMock, 'GET', '/api/dashboard/activity').map(([url]) => String(url))).toContain('/api/dashboard/activity?days=30'),
    )
  })
})
