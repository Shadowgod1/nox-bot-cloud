import { pushToast } from '../components/toast'
import { ru } from '../i18n/ru'

/**
 * Fetch wrapper for the iler-core API.
 * - always `credentials: 'include'` (session cookie `iler_session`);
 * - mutations carry `X-ILER-CSRF` = cookie `iler_csrf`;
 * - 401 -> /login (first-run setup is detected via GET /api/setup/state, core has no "setup required" error);
 * - errors `{error:{code,message}}` become ApiError (known codes get Russian text) and a toast (unless `silent`).
 */

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

export interface RequestOptions {
  /** Do not show a toast on failure (caller renders the error itself). */
  silent?: boolean
  /** Do not redirect to /login on 401 (login form, session probe on public pages). */
  noAuthRedirect?: boolean
  signal?: AbortSignal
  /** Extra request headers (e.g. X-ILER-Setup-Token on the first-run owner request). */
  headers?: Record<string, string>
}

type Navigator = (path: string) => void

let navigator: Navigator = (path) => {
  if (window.location.pathname !== path) window.location.assign(path)
}

/** The router registers its navigate() here so redirects stay inside the SPA. */
export function setNavigator(fn: Navigator) {
  navigator = fn
}

export function getCookie(name: string): string | null {
  const prefix = `${name}=`
  for (const part of document.cookie.split(';')) {
    const c = part.trim()
    if (c.startsWith(prefix)) {
      try {
        return decodeURIComponent(c.slice(prefix.length))
      } catch {
        return c.slice(prefix.length)
      }
    }
  }
  return null
}

const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])
const PUBLIC_PATHS = ['/login', '/setup']

async function transport(input: string, init: RequestInit): Promise<Response> {
  if (import.meta.env.VITE_MOCK === '1') {
    const { mockFetch } = await import('./mock')
    return mockFetch(input, init)
  }
  return fetch(input, init)
}

async function parseBody(res: Response): Promise<unknown> {
  if (res.status === 204) return undefined
  const type = res.headers.get('Content-Type') ?? ''
  if (type.includes('application/json')) {
    try {
      return await res.json()
    } catch {
      return undefined
    }
  }
  const text = await res.text()
  return text === '' ? undefined : text
}

function errorFrom(status: number, body: unknown): ApiError {
  const e = (body as { error?: { code?: unknown; message?: unknown } } | undefined)?.error
  if (e && typeof e.message === 'string') {
    const code = typeof e.code === 'string' ? e.code : 'error'
    const text = ru.errorCodes[code]
    if (!text) return new ApiError(status, code, e.message)
    const withDetail = ru.errorDetail.includes(code) && e.message && e.message !== code
    return new ApiError(status, code, withDetail ? `${text} (${e.message})` : text)
  }
  // FastAPI validation errors / plain detail strings.
  const detail = (body as { detail?: unknown } | undefined)?.detail
  if (typeof detail === 'string') return new ApiError(status, 'error', detail)
  if (status === 422) return new ApiError(status, 'validation', ru.errors.validation)
  if (status === 403) return new ApiError(status, 'forbidden', ru.errors.forbidden)
  if (status === 429) return new ApiError(status, 'rate_limited', ru.errors.rateLimited)
  if (status >= 500) return new ApiError(status, 'server', ru.errors.server)
  return new ApiError(status, 'error', ru.errors.generic)
}

export async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  opts: RequestOptions = {},
): Promise<T> {
  const headers = new Headers({ ...opts.headers, Accept: 'application/json' })
  if (MUTATING.has(method)) {
    const csrf = getCookie('iler_csrf')
    if (csrf) headers.set('X-ILER-CSRF', csrf)
  }
  const init: RequestInit = { method, headers, credentials: 'include', signal: opts.signal }
  if (body !== undefined) {
    headers.set('Content-Type', 'application/json')
    init.body = JSON.stringify(body)
  }

  let res: Response
  try {
    res = await transport(path, init)
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') throw err
    const e = new ApiError(0, 'network', ru.errors.network)
    if (!opts.silent) pushToast(e.message)
    throw e
  }

  const data = await parseBody(res)
  if (res.ok) return data as T

  const err = errorFrom(res.status, data)
  const onPublic = PUBLIC_PATHS.includes(window.location.pathname)
  if (res.status === 401 && !opts.noAuthRedirect) {
    if (!onPublic) navigator('/login')
    throw err
  }
  if (!opts.silent) pushToast(err.message)
  throw err
}

export const api = {
  get: <T>(path: string, opts?: RequestOptions) => request<T>('GET', path, undefined, opts),
  post: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
    request<T>('POST', path, body ?? {}, opts),
  put: <T>(path: string, body: unknown, opts?: RequestOptions) =>
    request<T>('PUT', path, body, opts),
  del: <T>(path: string, opts?: RequestOptions) => request<T>('DELETE', path, undefined, opts),
}

/** Build a query string, skipping empty values. */
export function qs(params: Record<string, string | number | null | undefined>): string {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') p.set(k, String(v))
  }
  const s = p.toString()
  return s ? `?${s}` : ''
}

/** Server-Sent Events source (real or mock). */
export async function openEventSource(url: string): Promise<EventSource> {
  if (import.meta.env.VITE_MOCK === '1') {
    const { MockEventSource } = await import('./mock')
    return new MockEventSource(url) as unknown as EventSource
  }
  return new EventSource(url, { withCredentials: true })
}
