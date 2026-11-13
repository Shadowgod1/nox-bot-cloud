import type { Conversation, ConversationRow, Message, ServerEvent } from '../../api/types'

type DataOf<K extends ServerEvent['type']> = Extract<ServerEvent, { type: K }>['data']

/** `message.new` carries `{conversation_id, message}`. */
export function normalizeMessageEvent(data: DataOf<'message.new'>): Message {
  return data.message
}

/**
 * `conversation.updated` comes in two shapes: the engine publishes `{conversation: row}` (no
 * `last_message`); older cores published the flat Conversation from the API (takeover / release /
 * unread reset), which is still accepted.
 * The result is a patch: `last_message` is present only when the server sent it.
 */
export function normalizeConversationEvent(
  data: DataOf<'conversation.updated'>,
): ConversationRow & Partial<Pick<Conversation, 'last_message'>> {
  const row = 'conversation' in data ? data.conversation : data
  const { created_at: _created, ...rest } = row as ConversationRow
  return rest
}

/** ISO timestamps from core use both "Z" and "+00:00": compare as dates, not strings. */
export function byLastMessageDesc(a: Conversation, b: Conversation): number {
  const ta = a.last_message_at ? Date.parse(a.last_message_at) : 0
  const tb = b.last_message_at ? Date.parse(b.last_message_at) : 0
  return tb - ta || b.id - a.id
}
