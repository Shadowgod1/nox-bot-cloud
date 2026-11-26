import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { Layout, PageBody } from './Layout'
import { AuthProvider } from '../hooks/useAuth'
import { FakeEventSource, mockApi } from '../test/utils'

function renderLayout() {
  mockApi({
    'GET /api/setup/state': { needs_setup: false },
    'GET /api/auth/me': { id: 1, email: 'owner@club.kz', name: 'Owner', role: 'owner' },
  })
  return render(
    <MemoryRouter initialEntries={['/']}>
      <AuthProvider>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<p>home</p>} />
            <Route path="bookings" element={<p>bookings page</p>} />
          </Route>
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe('Layout', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', FakeEventSource)
  })

  it('phone menu opens the navigation drawer and closes it after navigating', async () => {
    renderLayout()
    await screen.findByText('Owner')
    const menu = screen.getByRole('button', { name: 'Меню' })
    const nav = screen.getByRole('complementary', { name: 'Основная навигация' })
    expect(menu).toHaveAttribute('aria-expanded', 'false')
    expect(nav.className).toContain('max-md:invisible')

    await userEvent.click(menu)
    expect(menu).toHaveAttribute('aria-expanded', 'true')
    expect(nav.className).not.toContain('max-md:invisible')

    await userEvent.click(screen.getByRole('link', { name: 'Заявки' }))
    expect(await screen.findByText('bookings page')).toBeInTheDocument()
    expect(menu).toHaveAttribute('aria-expanded', 'false')
    expect(nav.className).toContain('max-md:invisible')
  })

  it('Escape closes the drawer', async () => {
    renderLayout()
    await screen.findByText('Owner')
    const menu = screen.getByRole('button', { name: 'Меню' })
    await userEvent.click(menu)
    await userEvent.keyboard('{Escape}')
    expect(menu).toHaveAttribute('aria-expanded', 'false')
  })

  it('PageBody spans the work area: no centred narrow column', () => {
    render(<PageBody>content</PageBody>)
    const cls = screen.getByText('content').className
    expect(cls).toContain('w-full')
    expect(cls).not.toMatch(/max-w-\[1[0-2]\d\dpx\]/)
  })
})
