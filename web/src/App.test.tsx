import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { App } from './App'
import { FakeEventSource, mockApi } from './test/utils'

describe('App routing', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', FakeEventSource)
  })

  it('redirects to setup when needs_setup', async () => {
    mockApi({ 'GET /api/setup/state': { needs_setup: true } })
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    )
    expect(await screen.findByRole('heading', { name: 'Создайте учётную запись владельца' })).toBeInTheDocument()
  })

  it('redirects to login when not authenticated', async () => {
    mockApi({
      'GET /api/setup/state': { needs_setup: false },
      'GET /api/auth/me': new Response(JSON.stringify({ error: { code: 'unauthorized', message: 'Нужен вход' } }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      }),
    })
    render(
      <MemoryRouter initialEntries={['/bookings']}>
        <App />
      </MemoryRouter>,
    )
    expect(await screen.findByRole('heading', { name: 'Вход в ILER' })).toBeInTheDocument()
  })
})
