import type { AnthropicEffort } from '../../api/types'

/** Shared by the setup wizard and System → LLM settings. */
export const ANTHROPIC_DEFAULT_MODEL = 'claude-sonnet-5-5'
export const ANTHROPIC_EFFORTS: readonly (readonly [AnthropicEffort, string])[] = [
  ['low', 'Низкая'],
  ['medium', 'Средняя'],
  ['high', 'Высокая'],
]
export const ANTHROPIC_EFFORT_HINT = 'Насколько глубоко Claude обдумывает ответ. Низкая — самые быстрые ответы.'
