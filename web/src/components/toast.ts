// Tiny global toast store: the API client and pages push, <ToastHost> renders.

export type ToastKind = 'error' | 'success' | 'info'

export interface Toast {
  id: number
  kind: ToastKind
  message: string
}

type Listener = (items: Toast[]) => void

let items: Toast[] = []
let seq = 0
const listeners = new Set<Listener>()

function emit() {
  for (const l of listeners) l(items)
}

export function pushToast(message: string, kind: ToastKind = 'error', ttlMs = 6000): number {
  const id = ++seq
  // Collapse identical consecutive messages (e.g. several failing requests at once).
  if (items.some((t) => t.message === message && t.kind === kind)) return id
  items = [...items, { id, kind, message }].slice(-4)
  emit()
  if (ttlMs > 0) setTimeout(() => dismissToast(id), ttlMs)
  return id
}

export function dismissToast(id: number) {
  const next = items.filter((t) => t.id !== id)
  if (next.length !== items.length) {
    items = next
    emit()
  }
}

export function subscribeToasts(listener: Listener): () => void {
  listeners.add(listener)
  listener(items)
  return () => {
    listeners.delete(listener)
  }
}

export const toast = {
  error: (m: string) => pushToast(m, 'error'),
  success: (m: string) => pushToast(m, 'success', 3500),
  info: (m: string) => pushToast(m, 'info', 4500),
}
