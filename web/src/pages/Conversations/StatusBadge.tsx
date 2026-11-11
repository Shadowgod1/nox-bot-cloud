import type { Conversation } from '../../api/types'
import { Badge } from '../../components/ui'
import { formatTime, ru } from '../../i18n/ru'

export function StatusBadge({ conversation: c }: { conversation: Pick<Conversation, 'status' | 'paused_until'> }) {
  switch (c.status) {
    case 'needs_operator':
      return <Badge tone="danger" dot>{ru.convStatus.needs_operator}</Badge>
    case 'paused':
      return (
        <Badge tone="warn" dot>
          {c.paused_until ? `Оператор, бот молчит до ${formatTime(c.paused_until)}` : 'Оператор ведёт диалог'}
        </Badge>
      )
    case 'closed':
      return <Badge>{ru.convStatus.closed}</Badge>
    default:
      return <Badge tone="ok" dot>{ru.convStatus.active}</Badge>
  }
}
