import type { Conversation } from '../../api/types'
import { Badge } from '../../components/ui'
import { formatDateTime, formatTime, ru } from '../../i18n/ru'

/** "21:30" today, otherwise with the date ("27 нояб., 09:30"). */
export function untilLabel(iso: string, now = new Date()): string {
  const d = new Date(iso)
  return d.toDateString() === now.toDateString() ? formatTime(iso) : formatDateTime(iso)
}

export function StatusBadge({ conversation: c }: { conversation: Pick<Conversation, 'status' | 'paused_until'> }) {
  switch (c.status) {
    case 'needs_operator':
      return <Badge tone="danger" dot>{ru.convStatus.needs_operator}</Badge>
    case 'paused':
      return (
        <Badge tone="warn" dot>
          {c.paused_until ? `Ведёт оператор до ${untilLabel(c.paused_until)}` : 'Ведёт оператор'}
        </Badge>
      )
    case 'closed':
      return <Badge>{ru.convStatus.closed}</Badge>
    default:
      return <Badge tone="ok" dot>{ru.convStatus.active}</Badge>
  }
}
