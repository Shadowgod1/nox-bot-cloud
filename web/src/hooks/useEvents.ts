import { useEffect, useRef } from 'react'
import { openEventSource } from '../api/client'
import type { ServerEvent } from '../api/types'

/**
 * One shared EventSource on /api/events for the whole app.
 * Opened lazily on the first subscriber, closed when the last one leaves.
 * EventSource reconnects by itself after network drops.
 */

type EventType = ServerEvent['type']
type DataOf<K extends EventType> = Extract<ServerEvent, { type: K }>['data']
type Handler = (data: unknown) => void

const EVENT_TYPES: EventType[] = ['message.new', 'conversation.updated', 'booking.updated', 'whatsapp.status', 'bot.status']

const subscribers = new Map<EventType, Set<Handler>>()
let source: EventSource | null = null
let opening: Promise<void> | null = null
let generation = 0

function total() {
  let n = 0
  subscribers.forEach((s) => (n += s.size))
  return n
}

function dispatch(type: EventType, raw: string) {
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    return
  }
  subscribers.get(type)?.forEach((h) => h(data))
}

function ensureOpen() {
  if (source || opening) return
  const gen = generation
  opening = openEventSource('/api/events')
    .then((es) => {
      if (gen !== generation || total() === 0) {
        es.close()
        return
      }
      source = es
      for (const t of EVENT_TYPES) es.addEventListener(t, (e) => dispatch(t, (e as MessageEvent).data))
    })
    .catch(() => undefined)
    .finally(() => {
      opening = null
    })
}

function maybeClose() {
  if (total() === 0 && source) {
    source.close()
    source = null
  }
}

export function subscribe<K extends EventType>(type: K, handler: (data: DataOf<K>) => void): () => void {
  if (!subscribers.has(type)) subscribers.set(type, new Set())
  const h = handler as Handler
  subscribers.get(type)!.add(h)
  ensureOpen()
  return () => {
    subscribers.get(type)?.delete(h)
    // Delay so route changes (unmount -> mount) reuse the connection.
    setTimeout(maybeClose, 1000)
  }
}

/** Subscribe a component to a server event; the latest handler is always used. */
export function useEvent<K extends EventType>(type: K, handler: (data: DataOf<K>) => void) {
  const ref = useRef(handler)
  ref.current = handler
  useEffect(() => subscribe(type, (d) => ref.current(d)), [type])
}

export function __resetEventsForTests() {
  generation++
  source?.close()
  source = null
  opening = null
  subscribers.clear()
}
