import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { AlertTriangle, WifiOff } from 'lucide-react'
import { api } from '../api/client'
import type { WhatsAppStatus } from '../api/types'
import { useEvent } from '../hooks/useEvents'

const REFRESH_MS = 60_000

/** App-wide banner when WhatsApp is disconnected or sending fails (spec 5.2). */
export function StatusBanner() {
  const [wa, setWa] = useState<WhatsAppStatus | null>(null)

  useEffect(() => {
    let alive = true
    const load = () =>
      api
        .get<WhatsAppStatus>('/api/whatsapp/status', { silent: true })
        .then((s) => alive && setWa(s))
        .catch(() => undefined)
    void load()
    const t = setInterval(load, REFRESH_MS)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [])

  useEvent('whatsapp.status', (s) => setWa(s))

  if (!wa || wa.status === 'open' || wa.status === 'connecting') return null

  const degraded = wa.status === 'degraded'
  return (
    <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-danger/20 bg-danger-soft px-4 py-2.5 text-sm text-ink sm:px-6 lg:px-8">
      {degraded ? <AlertTriangle className="size-4 shrink-0 text-danger" aria-hidden /> : <WifiOff className="size-4 shrink-0 text-danger" aria-hidden />}
      <span className="font-medium">
        {degraded ? 'WhatsApp подключён, но сообщения не отправляются.' : 'WhatsApp не подключён — бот не получает и не отправляет сообщения.'}
      </span>
      <Link to="/?connect=1" className="font-medium text-danger underline">
        Подключить заново
      </Link>
    </div>
  )
}
