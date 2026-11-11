import { vi } from 'vitest'

export function jsonResponse(body: unknown, status = 200) {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

type Route = (init: RequestInit, url: string) => unknown

/**
 * Stub global fetch with a table of "METHOD /path" -> handler (or static body).
 * Unknown routes answer 404 in the API error format.
 */
export function mockApi(table: Record<string, Route | unknown>) {
  const fn = vi.fn(async (input: string, init: RequestInit = {}) => {
    const method = (init.method ?? 'GET').toUpperCase()
    const path = input.split('?')[0]
    const key = `${method} ${path}`
    if (key in table) {
      const h = table[key]
      const body = typeof h === 'function' ? await (h as Route)(init, input) : h
      return body instanceof Response ? body : jsonResponse(body)
    }
    return jsonResponse({ error: { code: 'not_found', message: `no route ${key}` } }, 404)
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

export function callsTo(fn: ReturnType<typeof vi.fn>, method: string, path: string) {
  return fn.mock.calls.filter(([url, init]) => {
    const m = ((init as RequestInit | undefined)?.method ?? 'GET').toUpperCase()
    return m === method && String(url).split('?')[0] === path
  })
}

/** Controllable EventSource double. */
export class FakeEventSource {
  static instances: FakeEventSource[] = []
  url: string
  withCredentials: boolean
  readyState = 1
  onerror: ((e: Event) => void) | null = null
  onopen: ((e: Event) => void) | null = null
  private handlers = new Map<string, Set<(e: MessageEvent) => void>>()

  constructor(url: string, init?: EventSourceInit) {
    this.url = url
    this.withCredentials = Boolean(init?.withCredentials)
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set())
    this.handlers.get(type)!.add(fn)
  }

  removeEventListener(type: string, fn: (e: MessageEvent) => void) {
    this.handlers.get(type)?.delete(fn)
  }

  emit(type: string, data: unknown) {
    const ev = new MessageEvent(type, { data: JSON.stringify(data) })
    this.handlers.get(type)?.forEach((fn) => fn(ev))
  }

  close() {
    this.readyState = 2
  }
}
