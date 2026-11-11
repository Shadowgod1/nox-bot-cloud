import { describe, it, expect, vi, beforeEach } from 'vitest'
import { api, ApiError, setNavigator } from './client'
import { subscribeToasts } from '../components/toast'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('api client', () => {
  let fetchMock: ReturnType<typeof vi.fn>
  const navigate = vi.fn()

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }))
    vi.stubGlobal('fetch', fetchMock)
    navigate.mockReset()
    setNavigator(navigate)
    window.history.pushState({}, '', '/')
  })

  it('client adds csrf header on POST', async () => {
    document.cookie = 'iler_csrf=tok%2Ben123'
    await api.post('/api/conversations/1/takeover')
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/conversations/1/takeover')
    expect(init.method).toBe('POST')
    expect(init.credentials).toBe('include')
    expect(new Headers(init.headers).get('X-ILER-CSRF')).toBe('tok+en123')
  })

  it('does not send csrf header on GET but still includes credentials', async () => {
    document.cookie = 'iler_csrf=abc'
    await api.get('/api/dashboard')
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(init.credentials).toBe('include')
    expect(new Headers(init.headers).get('X-ILER-CSRF')).toBeNull()
  })

  it('sends JSON body on PUT', async () => {
    await api.put('/api/settings/bot', { name: 'x' })
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(init.body).toBe('{"name":"x"}')
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json')
  })

  it('redirects to /login on 401', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: 'unauthorized', message: 'Нужен вход' } }, 401),
    )
    await expect(api.get('/api/dashboard')).rejects.toBeInstanceOf(ApiError)
    expect(navigate).toHaveBeenCalledWith('/login')
  })

  it('redirects to /setup when server says setup is required', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: 'setup_required', message: 'setup' } }, 409),
    )
    await expect(api.get('/api/dashboard')).rejects.toBeInstanceOf(ApiError)
    expect(navigate).toHaveBeenCalledWith('/setup')
  })

  it('shows API error message as a toast', async () => {
    const seen: string[] = []
    const unsub = subscribeToasts((items) => seen.push(...items.map((t) => t.message)))
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: 'bad_request', message: 'Неверный номер' } }, 400),
    )
    const err = await api.post('/api/whatsapp/pairing', { phone: '1' }).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).code).toBe('bad_request')
    expect(seen).toContain('Неверный номер')
    unsub()
  })

  it('does not toast when silent', async () => {
    const seen: string[] = []
    const unsub = subscribeToasts((items) => seen.push(...items.map((t) => t.message)))
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: 'bad_request', message: 'Тихо' } }, 400),
    )
    await api.get('/api/x', { silent: true }).catch(() => undefined)
    expect(seen).not.toContain('Тихо')
    unsub()
  })
})
