import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { App } from './App'
import { BRAND_KEY, THEME_KEY, getTheme, setBrand } from './brand'
import { Layout } from './components/Layout'
import { AuthProvider } from './hooks/useAuth'
import { Login } from './pages/Login/Login'
import { FakeEventSource, mockApi } from './test/utils'

const html = document.documentElement

function renderLogin() {
  mockApi({ 'GET /api/setup/state': { needs_setup: false } })
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <AuthProvider>
        <Login />
      </AuthProvider>
    </MemoryRouter>,
  )
}

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
          </Route>
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe('brand copy', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', FakeEventSource)
  })

  it('ILER by default: login heading, wordmark, title, no theme switch', async () => {
    renderLogin()
    expect(screen.getByRole('heading', { name: 'Вход в ILER' })).toBeInTheDocument()
    expect(screen.getByLabelText('ILER')).toBeInTheDocument()
    expect(document.title).toBe('ILER')
    expect(html.getAttribute('data-brand')).toBe('iler')
    expect(html.hasAttribute('data-theme')).toBe(false)
  })

  it('Nox: login heading, wordmark, title and favicon switch with the brand', async () => {
    setBrand('nox')
    renderLogin()
    expect(screen.getByRole('heading', { name: 'Вход в Nox' })).toBeInTheDocument()
    expect(screen.getByLabelText('Nox Bot')).toBeInTheDocument()
    expect(screen.queryByText(/ILER/)).not.toBeInTheDocument()
    expect(document.title).toBe('Nox Bot')
    expect(html.getAttribute('data-brand')).toBe('nox')
    expect(html.getAttribute('data-theme')).toBe('light')
    expect(window.localStorage.getItem(BRAND_KEY)).toBe('nox')
  })

  it('re-renders mounted screens when the brand changes', async () => {
    renderLogin()
    expect(screen.getByRole('heading', { name: 'Вход в ILER' })).toBeInTheDocument()
    act(() => setBrand('nox'))
    expect(screen.getByRole('heading', { name: 'Вход в Nox' })).toBeInTheDocument()
  })

  it('takes the brand from GET /api/brand before login', async () => {
    mockApi({
      'GET /api/brand': { brand: 'nox' },
      'GET /api/setup/state': { needs_setup: false },
      'GET /api/auth/me': new Response(JSON.stringify({ error: { code: 'unauthorized', message: 'Нужен вход' } }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      }),
    })
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    )
    expect(await screen.findByRole('heading', { name: 'Вход в Nox' })).toBeInTheDocument()
    expect(document.title).toBe('Nox Bot')
  })

  it('keeps the cached brand when the server has no /api/brand (older core)', async () => {
    setBrand('nox')
    mockApi({
      'GET /api/setup/state': { needs_setup: false },
      'GET /api/auth/me': new Response(JSON.stringify({ error: { code: 'unauthorized', message: 'Нужен вход' } }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      }),
    })
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    )
    expect(await screen.findByRole('heading', { name: 'Вход в Nox' })).toBeInTheDocument()
  })

  it('Layout: ILER wordmark and no theme switch', async () => {
    renderLayout()
    expect(await screen.findByText('Owner')).toBeInTheDocument()
    expect(screen.getByLabelText('ILER')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Тёмная тема' })).not.toBeInTheDocument()
  })

  it('Layout: Nox wordmark and theme switch', async () => {
    setBrand('nox')
    renderLayout()
    expect(await screen.findByText('Owner')).toBeInTheDocument()
    expect(screen.getByLabelText('Nox Bot')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Тёмная тема' })).toBeInTheDocument()
  })
})

describe('theme toggle (Nox only)', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', FakeEventSource)
  })

  it('switches data-theme and remembers the choice', async () => {
    setBrand('nox')
    renderLayout()
    await userEvent.click(await screen.findByRole('button', { name: 'Тёмная тема' }))
    expect(html.getAttribute('data-theme')).toBe('dark')
    expect(window.localStorage.getItem(THEME_KEY)).toBe('dark')
    expect(getTheme()).toBe('dark')

    await userEvent.click(screen.getByRole('button', { name: 'Светлая тема' }))
    expect(html.getAttribute('data-theme')).toBe('light')
    expect(window.localStorage.getItem(THEME_KEY)).toBe('light')
  })

  it('ILER never gets a data-theme, even with a stored dark choice', async () => {
    setBrand('nox')
    renderLayout()
    await userEvent.click(await screen.findByRole('button', { name: 'Тёмная тема' }))
    act(() => setBrand('iler'))
    expect(html.hasAttribute('data-theme')).toBe(false)
    act(() => setBrand('nox'))
    expect(html.getAttribute('data-theme')).toBe('dark')
  })

  it('works when localStorage throws', async () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    setBrand('nox')
    renderLayout()
    await userEvent.click(await screen.findByRole('button', { name: 'Тёмная тема' }))
    await waitFor(() => expect(html.getAttribute('data-theme')).toBe('dark'))
    spy.mockRestore()
  })
})
