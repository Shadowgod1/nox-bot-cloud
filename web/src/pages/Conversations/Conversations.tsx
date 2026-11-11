import { useEffect, useMemo, useState } from 'react'
import { NavLink, useParams } from 'react-router'
import { MessagesSquare, Search } from 'lucide-react'
import { api, qs } from '../../api/client'
import type { Conversation, ConversationStatus, Page } from '../../api/types'
import { useLoad } from '../../hooks/useLoad'
import { useEvent } from '../../hooks/useEvents'
import { EmptyState, ErrorState, Input, Segmented, Spinner, cx } from '../../components/ui'
import { formatShort } from '../../i18n/ru'
import { ChatView } from './ChatView'
import { StatusBadge } from './StatusBadge'
import { normalizeMessageEvent } from './normalize'

type Filter = 'all' | ConversationStatus


function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

export function Conversations() {
  const { id } = useParams()
  const selectedId = id ? Number(id) : null
  const [filter, setFilter] = useState<Filter>('all')
  const [query, setQuery] = useState('')
  const q = useDebounced(query.trim(), 300)

  const list = useLoad(
    () =>
      api.get<Page<Conversation>>(
        `/api/conversations${qs({ status: filter === 'all' ? null : filter, q })}`,
        { silent: true },
      ),
    [filter, q],
  )
  const items = useMemo(() => list.data?.items ?? [], [list.data])
  const selected = items.find((c) => c.id === selectedId) ?? null

  const patch = (cid: number, fn: (c: Conversation) => Conversation) =>
    list.setData((d) => (d ? { ...d, items: d.items.map((c) => (c.id === cid ? fn(c) : c)) } : d))

  useEvent('message.new', (data) => {
    const m = normalizeMessageEvent(data)
    const known = items.some((c) => c.id === m.conversation_id)
    if (!known) {
      void list.reload()
      return
    }
    list.setData((d) => {
      if (!d) return d
      const updated = d.items.map((c) =>
        c.id === m.conversation_id
          ? {
              ...c,
              last_text: m.text,
              last_message_at: m.created_at,
              unread: c.id === selectedId || m.direction === 'out' ? 0 : c.unread + 1,
            }
          : c,
      )
      updated.sort((a, b) => (b.last_message_at ?? '').localeCompare(a.last_message_at ?? ''))
      return { ...d, items: updated }
    })
  })

  useEvent('conversation.updated', (data) => {
    if (items.some((c) => c.id === data.id)) patch(data.id, (c) => ({ ...c, ...data }))
    else void list.reload()
  })

  // Opening a conversation marks it read locally (server resets `unread` on fetch).
  useEffect(() => {
    if (selectedId) patch(selectedId, (c) => (c.unread ? { ...c, unread: 0 } : c))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId])

  const needsOperator = items.filter((c) => c.status === 'needs_operator').length

  return (
    <div className="flex h-full min-h-0">
      <nav
        aria-label="Список диалогов"
        className={cx(
          'flex w-full min-w-0 flex-col border-r border-line bg-surface md:w-[300px] md:shrink-0 lg:w-[340px]',
          selectedId != null && 'hidden md:flex',
        )}
      >
        <div className="flex flex-col gap-3 border-b border-line p-4">
          <h1 className="text-[18px] font-semibold text-ink">Диалоги</h1>
          <div className="relative">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-ink-3" aria-hidden />
            <Input
              type="search"
              aria-label="Поиск по имени или телефону"
              placeholder="Имя или телефон"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="pl-8"
            />
          </div>
          <Segmented<Filter>
            label="Фильтр по статусу"
            value={filter}
            onChange={setFilter}
            options={[
              { value: 'all', label: 'Все' },
              { value: 'needs_operator', label: 'Оператор', count: filter === 'all' ? needsOperator : undefined },
              { value: 'active', label: 'Бот' },
              { value: 'paused', label: 'Пауза' },
              { value: 'closed', label: 'Закрытые' },
            ]}
          />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {list.loading && !list.data ? (
            <Spinner />
          ) : list.error && !list.data ? (
            <ErrorState message={list.error.message} onRetry={list.reload} />
          ) : items.length === 0 ? (
            <EmptyState title={q || filter !== 'all' ? 'Ничего не найдено' : 'Диалогов пока нет'}>
              {q || filter !== 'all'
                ? 'Измените фильтр или строку поиска.'
                : 'Когда клиенты напишут в WhatsApp, переписки появятся здесь.'}
            </EmptyState>
          ) : (
            <ul>
              {items.map((c) => (
                <li key={c.id}>
                  <NavLink
                    to={`/conversations/${c.id}`}
                    className={({ isActive }) =>
                      cx(
                        'flex flex-col gap-1 border-b border-line/70 px-4 py-3 transition-colors duration-100',
                        isActive ? 'bg-accent-soft' : 'hover:bg-sunken',
                      )
                    }
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <span className={cx('truncate text-sm', c.unread ? 'font-semibold text-ink' : 'font-medium text-ink')}>
                        {c.name || c.phone}
                      </span>
                      <span className="tabular shrink-0 text-[12px] text-ink-3">{formatShort(c.last_message_at)}</span>
                    </div>
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-[13px] text-ink-2">{c.last_text || (c.name ? c.phone : '—')}</span>
                      {c.unread > 0 && (
                        <span
                          className="tabular grid h-5 min-w-5 shrink-0 place-items-center rounded-full bg-accent px-1.5 text-[11px] font-semibold text-white"
                          aria-label={`Непрочитанных: ${c.unread}`}
                        >
                          {c.unread}
                        </span>
                      )}
                    </div>
                    {c.status !== 'active' && (
                      <div>
                        <StatusBadge conversation={c} />
                      </div>
                    )}
                  </NavLink>
                </li>
              ))}
            </ul>
          )}
        </div>
      </nav>

      <section className={cx('min-w-0 flex-1 flex-col bg-canvas', selectedId ? 'flex' : 'hidden md:flex')} aria-label="Переписка">
        {selectedId ? (
          <ChatView
            key={selectedId}
            conversationId={selectedId}
            conversation={selected}
            onConversation={(c) => patch(c.id, (old) => ({ ...old, ...c }))}
          />
        ) : (
          <div className="grid flex-1 place-items-center">
            <EmptyState title="Выберите диалог" icon={<MessagesSquare className="size-5" />}>
              Откройте переписку слева, чтобы прочитать её, ответить клиенту или перехватить диалог у бота.
            </EmptyState>
          </div>
        )}
      </section>
    </div>
  )
}

