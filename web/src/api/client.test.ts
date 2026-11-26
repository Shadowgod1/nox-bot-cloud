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

  it('does not redirect on a 409 conflict (setup is detected via /api/setup/state)', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: 'already_set_up', message: 'Setup is already complete' } }, 409),
    )
    const err = await api.post('/api/setup/owner', {}).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(409)
    expect(navigate).not.toHaveBeenCalled()
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

  it('translates known core error codes, keeping the detail where it helps', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: 'invalid_credentials', message: 'Wrong e-mail or password' } }, 401),
    )
    const e1 = await api.post('/api/auth/login', {}, { silent: true, noAuthRedirect: true }).catch((e) => e)
    expect((e1 as ApiError).message).toBe('Неверный e-mail или пароль.')
    expect((e1 as ApiError).code).toBe('invalid_credentials')

    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: 'validation_error', message: 'body.text: text must not be blank' } }, 422),
    )
    const e2 = await api.post('/api/conversations/1/send', { text: ' ' }, { silent: true }).catch((e) => e)
    expect((e2 as ApiError).message).toBe('Проверьте заполнение полей. (body.text: text must not be blank)')
  })

  it('shows the core reason for 503/424 upstream errors', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: 'llm_error', message: 'anthropic: Провайдер модели отклонил ключ доступа.' } }, 424),
    )
    const e1 = await api.post('/api/test-chat', {}, { silent: true }).catch((e) => e)
    expect((e1 as ApiError).status).toBe(424)
    expect((e1 as ApiError).message).toBe('Ошибка языковой модели. (anthropic: Провайдер модели отклонил ключ доступа.)')

    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: 'core_unavailable', message: 'Сервер перезапускается' } }, 503),
    )
    const e2 = await api.get('/api/dashboard', { silent: true }).catch((e) => e)
    expect((e2 as ApiError).message).toContain('Сервер перезапускается')
  })

  it('replaces a proxy HTML page with a Russian message carrying the status', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response('<html><body>The origin web server returned an invalid or incomplete response to Cloudflare</body></html>', {
        status: 502,
        headers: { 'Content-Type': 'text/html' },
      }),
    )
    const err = await api.post('/api/test-chat', {}, { silent: true }).catch((e) => e)
    expect((err as ApiError).code).toBe('invalid_response')
    expect((err as ApiError).message).toContain('Сервер вернул некорректный ответ (код 502)')
    expect((err as ApiError).message).not.toContain('Cloudflare')
  })

  it('does not show the detail of a proxy JSON problem document', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ type: 'https://example.invalid/502', title: 'Bad gateway', status: 502,
        detail: 'The origin web server returned an invalid or incomplete response to Cloudflare.' }, 502),
    )
    const err = await api.post('/api/test-chat', {}, { silent: true }).catch((e) => e)
    expect((err as ApiError).message).toContain('Сервер вернул некорректный ответ (код 502)')
    expect((err as ApiError).message).not.toContain('origin web server')

    fetchMock.mockResolvedValueOnce(new Response('oops', { status: 500, headers: { 'Content-Type': 'text/plain' } }))
    const e2 = await api.get('/api/x', { silent: true }).catch((e) => e)
    expect((e2 as ApiError).message).toContain('(код 500)')
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
