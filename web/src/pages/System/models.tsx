import { useState } from 'react'
import { CircleAlert } from 'lucide-react'
import type { LlmProvider, LlmSettings } from '../../api/types'
import { useBrand, type BrandCopy } from '../../brand'
import { Input, Select } from '../../components/ui'

/** Shared by the setup wizard and System → LLM settings. */
export const MODEL_FIELD = { openai: 'openai_model', gemini: 'gemini_model', anthropic: 'anthropic_model' } as const

/** The bot runs on Claude Sonnet or Haiku only. */
export const CLAUDE_MODELS = [
  ['claude-sonnet-5-5', 'Claude Sonnet 5.5 — умнее, для сложных диалогов'],
  ['claude-haiku-5-5', 'Claude Haiku 5.5 — быстрее и дешевле'],
] as const

/** Shown until the provider's own list is loaded; preferred when the list contains them. */
export const RECOMMENDED: Record<Exclude<LlmProvider, 'anthropic'>, string[]> = {
  openai: ['gpt-5.4-mini', 'gpt-5.4-nano'],
  gemini: ['gemini-3.8-flash', 'gemini-3.5-flash-lite'],
}

export function modelHint(brand: BrandCopy): Record<LlmProvider, string> {
  return {
    openai: `Список моделей OpenAI, доступных через подключение ${brand.name}.`,
    gemini: `Список моделей Gemini, доступных через подключение ${brand.name}.`,
    anthropic: 'Sonnet отвечает умнее, Haiku — быстрее и в ~20 раз дешевле.',
  }
}

export const PROVIDER_LABEL: Record<LlmProvider, string> = {
  openai: 'OpenAI',
  gemini: 'Google Gemini',
  anthropic: 'Anthropic (Claude)',
}

/** Shown when the server has no vendor (ILER / Nox) key for any provider. */
export function noVendorKeysText(brand: BrandCopy): string {
  return `Ключи ${brand.name} не настроены. Обратитесь в поддержку ${brand.company} — техник добавит их при установке.`
}

export function NoVendorKeys() {
  const brand = useBrand()
  return (
    <div role="alert" className="flex items-start gap-3 rounded-[10px] border border-warn/40 bg-warn-soft px-4 py-3 text-sm text-ink">
      <CircleAlert className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
      <p>{noVendorKeysText(brand)}</p>
    </div>
  )
}

/** POST /api/settings/llm/models: models of the primary provider for (unsaved) settings. */
export interface LlmModelsResult {
  ok: boolean
  models: string[]
  error?: string
}

export function modelOf(s: Pick<LlmSettings, 'openai_model' | 'gemini_model' | 'anthropic_model'>, p: LlmProvider) {
  return s[MODEL_FIELD[p]]
}

/** Model to preselect: the current one if available, else the first recommended one listed, else the first. */
export function pickModel(provider: LlmProvider, current: string, models: string[]): string {
  if (models.length === 0 || models.includes(current)) return current
  if (provider !== 'anthropic') {
    const rec = RECOMMENDED[provider].find((m) => models.includes(m))
    if (rec) return rec
  }
  return models[0]
}

const OTHER = '__other__'

interface ModelSelectProps {
  id?: string
  provider: LlmProvider
  value: string
  onChange: (model: string) => void
  /** Provider list for OpenAI/Gemini (null until loaded). */
  models: string[] | null
}

export function ModelSelect({ id, provider, value, onChange, models }: ModelSelectProps) {
  const [custom, setCustom] = useState(false)

  if (provider === 'anthropic') {
    return (
      <Select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        {CLAUDE_MODELS.map(([model, label]) => (
          <option key={model} value={model}>
            {label}
          </option>
        ))}
      </Select>
    )
  }

  if (custom) {
    return (
      <div className="flex gap-2">
        <Input id={id} value={value} spellCheck={false} onChange={(e) => onChange(e.target.value)} className="flex-1" />
        <button type="button" onClick={() => setCustom(false)} className="text-[13px] text-ink-2 underline hover:text-ink">
          Из списка
        </button>
      </div>
    )
  }

  const recommended = RECOMMENDED[provider]
  const options = [...new Set([...(models ?? recommended), ...(value ? [value] : [])])]
  return (
    <Select
      id={id}
      value={value}
      onChange={(e) => (e.target.value === OTHER ? setCustom(true) : onChange(e.target.value))}
    >
      {options.map((m) => (
        <option key={m} value={m}>
          {recommended.includes(m) ? `${m} (рекомендуется)` : m}
        </option>
      ))}
      <option value={OTHER}>Другая модель…</option>
    </Select>
  )
}
