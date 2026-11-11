import type { Message } from '../../api/types'

/** `message.new` may carry the message itself or `{conversation_id, message}`. */
export function normalizeMessageEvent(data: Message | { conversation_id: number; message: Message }): Message {
  return 'message' in data && data.message ? data.message : (data as Message)
}
