import { describe, it, expect } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { RoiCard } from './RoiCard'
import { mockApi } from '../../test/utils'
import type { RoiStats } from '../../api/types'

const stats = (over: Partial<RoiStats> = {}): RoiStats => ({
  days: 7,
  since: '2026-11-20T00:00:00+05:00',
  bot_conversations: 38,
  bookings: 11,
  after_hours_conversations: 9,
  handovers: 4,
  coins: 212.4,
  hours_source: 'default',
  has_activity: true,
  ...over,
})

function renderCard(handler: (url: string) => RoiStats) {
  const fetchMock = mockApi({ 'GET /api/dashboard/roi': (_init: RequestInit, url: string) => handler(url) })
  render(
    <MemoryRouter>
      <RoiCard />
    </MemoryRouter>,
  )
  return fetchMock
}

describe('Что сделал бот', () => {
  it('shows an honest empty state for a new bot', async () => {
    renderCard(() => stats({ has_activity: false, bot_conversations: 0, bookings: 0, after_hours_conversations: 0, handovers: 0, coins: 1.2 }))
    expect(await screen.findByText(/Пока клиентов не было/)).toBeInTheDocument()
    expect(screen.getByText(/На проверку бота потрачено 1,2 коина/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Проверить бота' })).toHaveAttribute('href', '/test-chat')
    expect(screen.queryByTestId('roi-stats')).not.toBeInTheDocument()
  })

  it('shows the 7-day numbers and switches to 30 days', async () => {
    const user = userEvent.setup()
    const fetchMock = renderCard((url) => (url.includes('days=30') ? stats({ days: 30, bot_conversations: 156, hours_source: 'settings' }) : stats()))
    const list = await screen.findByTestId('roi-stats')
    expect(within(list).getByText('38')).toBeInTheDocument()
    expect(within(list).getByText('диалогов провёл сам')).toBeInTheDocument()
    expect(within(list).getByText('заявок собрал')).toBeInTheDocument()
    expect(within(list).getByText('с 21:00 до 09:00')).toBeInTheDocument()
    expect(within(list).getByText('212,4')).toBeInTheDocument()

    await user.click(screen.getByRole('radio', { name: '30 дней' }))
    expect(await within(screen.getByTestId('roi-stats')).findByText('156')).toBeInTheDocument()
    expect(screen.getByText('по часам из настроек')).toBeInTheDocument()
    expect(fetchMock.mock.calls.map(([u]) => String(u))).toContain('/api/dashboard/roi?days=30')
  })
})
