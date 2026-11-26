import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { vi } from 'vitest'

export function json(body: unknown, status = 200) {
  return new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

type Handler = (url: string, init: RequestInit) => Response | Promise<Response>

/** fetch stub: routes "METHOD /path" (query ignored) to handlers; records every call. */
export function mockFetch(routes: Record<string, Handler>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input)
    const key = `${init.method ?? 'GET'} ${url.split('?')[0]}`
    const handler = routes[key]
    if (!handler) return json({ error: { code: 'not_found', message: `no mock for ${key}` } }, 404)
    return handler(url, init)
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

export function bodyOf(fn: ReturnType<typeof mockFetch>, key: string): Record<string, unknown> {
  const call = fn.mock.calls.find(([u, i]) => `${(i as RequestInit)?.method ?? 'GET'} ${String(u).split('?')[0]}` === key)
  if (!call) throw new Error(`no call ${key}`)
  return JSON.parse(String((call[1] as RequestInit).body))
}

function Where() {
  const loc = useLocation()
  return <div data-testid="where">{loc.pathname}</div>
}

export function renderAt(path: string, element: ReactElement, state?: unknown) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: path, state }]}>
      <Routes>
        <Route path={path} element={element} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  )
}
