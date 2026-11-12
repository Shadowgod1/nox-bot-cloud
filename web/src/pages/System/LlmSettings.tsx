import { useEffect, useRef, useState } from 'react'
import { Zap } from 'lucide-react'
import { api } from '../../api/client'
import type { AnthropicEffort, LlmProvider, LlmProvidersResult, LlmSettings, LlmTestResult } from '../../api/types'
import { useLoad } from '../../hooks/useLoad'
import { Button, CheckLine, type CheckView, ErrorState, Field, Input, Panel, Select, Spinner } from '../../components/ui'
import { toast } from '../../components/toast'
import { ru } from '../../i18n/ru'

import { ANTHROPIC_DEFAULT_MODEL, ANTHROPIC_EFFORT_HINT, ANTHROPIC_EFFORTS } from './anthropic'
import { MODEL_HINT, ModelSelect, NoVendorKeys, PROVIDER_LABEL, type LlmModelsResult } from './models'

type Listable = 'openai' | 'gemini'

const TITLE = 'Языковая модель'
const DESCRIPTION = 'Провайдер и модели, на которых работает бот. Ключи доступа к провайдерам предоставляет ILER.'

/** Keep the selected providers within those ILER has keys for. */
function toForm(s: LlmSettings, available: LlmProvider[]): LlmSettings {
  return {
    ...s,
    provider: available.includes(s.provider) ? s.provider : (available[0] ?? s.provider),
    emergency_provider: s.emergency_provider && available.includes(s.emergency_provider) ? s.emergency_provider : null,
    anthropic_model: s.anthropic_model ?? ANTHROPIC_DEFAULT_MODEL,
    anthropic_effort: s.anthropic_effort ?? 'low',
  }
}

function toPayload(f: LlmSettings): LlmSettings {
  return {
    provider: f.provider,
    openai_model: f.openai_model,
    openai_fallback_model: f.openai_fallback_model?.trim() || null,
    gemini_model: f.gemini_model,
    anthropic_model: f.anthropic_model.trim() || ANTHROPIC_DEFAULT_MODEL,
    anthropic_effort: f.anthropic_effort,
    emergency_provider: f.emergency_provider,
  }
}

export function LlmSettingsPanel() {
  const { data, error, loading, reload } = useLoad(() =>
    Promise.all([
      api.get<LlmSettings>('/api/settings/llm', { silent: true }),
      api.get<LlmProvidersResult>('/api/settings/llm/providers', { silent: true }),
    ]),
  )
  const [form, setForm] = useState<LlmSettings | null>(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [check, setCheck] = useState<CheckView | null>(null)
  const [models, setModels] = useState<Record<Listable, string[] | null>>({ openai: null, gemini: null })
  const [listing, setListing] = useState<Listable | null>(null)
  const autoLoaded = useRef(false)

  const available = data?.[1].providers ?? []

  useEffect(() => {
    if (data) setForm(toForm(data[0], data[1].providers))
  }, [data])

  // Model lists for the available OpenAI / Gemini providers, once the settings are loaded.
  useEffect(() => {
    if (!form || autoLoaded.current) return
    autoLoaded.current = true
    void (async () => {
      for (const p of ['openai', 'gemini'] as const) if (available.includes(p)) await loadModels(p, true)
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form])

  if (loading && !form) return <Panel title={TITLE}><Spinner /></Panel>
  if (error && !form) return <Panel title={TITLE}><ErrorState message={error.message} onRetry={reload} /></Panel>
  if (!form) return null
  if (available.length === 0) {
    return (
      <Panel title={TITLE} description={DESCRIPTION}>
        <NoVendorKeys />
      </Panel>
    )
  }

  const set = <K extends keyof LlmSettings>(k: K, v: LlmSettings[K]) => setForm((f) => (f ? { ...f, [k]: v } : f))

  async function save(): Promise<boolean> {
    if (!form) return false
    setSaving(true)
    try {
      const saved = await api.put<LlmSettings>('/api/settings/llm', toPayload(form))
      setForm(toForm(saved ?? toPayload(form), available))
      toast.success(ru.common.saved)
      return true
    } catch {
      return false
    } finally {
      setSaving(false)
    }
  }

  async function test() {
    if (!form) return
    setTesting(true)
    setCheck(null)
    try {
      // Core checks the unsaved form values with ILER's key; nothing is saved.
      const r = await api.post<LlmTestResult>('/api/settings/llm/test', toPayload(form))
      setCheck({ ok: r.ok, message: r.ok ? undefined : r.error })
    } catch {
      /* toast */
    } finally {
      setTesting(false)
    }
  }

  async function loadModels(provider: Listable, quiet = false) {
    if (!form) return
    setListing(provider)
    try {
      const r = await api.post<LlmModelsResult>('/api/settings/llm/models', { ...toPayload(form), provider }, { silent: quiet })
      if (r.ok) setModels((m) => ({ ...m, [provider]: r.models }))
      else if (!quiet) toast.error(r.error ?? 'Не удалось получить список моделей')
    } catch {
      /* toast */
    } finally {
      setListing(null)
    }
  }

  const loadButton = (provider: Listable) => (
    <button
      type="button"
      onClick={() => void loadModels(provider)}
      disabled={listing !== null}
      className="self-start text-[13px] text-ink-2 underline hover:text-ink disabled:opacity-50"
    >
      {listing === provider ? 'Загружаем…' : models[provider] ? 'Обновить список моделей' : 'Загрузить список моделей'}
    </button>
  )

  const options = available.map((p) => (
    <option key={p} value={p}>
      {PROVIDER_LABEL[p]}
    </option>
  ))

  return (
    <Panel title={TITLE} description={DESCRIPTION}>
      <form
        className="grid gap-5"
        onSubmit={(e) => {
          e.preventDefault()
          void save()
        }}
      >
        <div className="grid gap-5 md:grid-cols-2">
          <Field label="Основной провайдер">
            <Select value={form.provider} onChange={(e) => set('provider', e.target.value as LlmProvider)}>
              {options}
            </Select>
          </Field>
          <Field label="Аварийный провайдер" hint="Используется, если основной недоступен.">
            <Select
              value={form.emergency_provider ?? ''}
              onChange={(e) => set('emergency_provider', (e.target.value || null) as LlmProvider | null)}
            >
              <option value="">Не использовать</option>
              {options}
            </Select>
          </Field>
        </div>

        {available.includes('openai') && (
          <fieldset className="grid gap-4 rounded-[9px] border border-line p-4">
            <legend className="px-1 text-[13px] font-semibold text-ink">OpenAI</legend>
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Основная модель OpenAI" hint={MODEL_HINT.openai}>
                <ModelSelect provider="openai" value={form.openai_model} models={models.openai} onChange={(m) => set('openai_model', m)} />
              </Field>
              <Field label="Резервная модель OpenAI" hint="Необязательно. Вызывается при ошибке основной.">
                <Input value={form.openai_fallback_model ?? ''} onChange={(e) => set('openai_fallback_model', e.target.value)} spellCheck={false} />
              </Field>
            </div>
            {loadButton('openai')}
          </fieldset>
        )}

        {available.includes('gemini') && (
          <fieldset className="grid gap-4 rounded-[9px] border border-line p-4">
            <legend className="px-1 text-[13px] font-semibold text-ink">Google Gemini</legend>
            <Field label="Модель Gemini" hint={MODEL_HINT.gemini} className="md:max-w-[calc(50%-0.5rem)]">
              <ModelSelect provider="gemini" value={form.gemini_model} models={models.gemini} onChange={(m) => set('gemini_model', m)} />
            </Field>
            {loadButton('gemini')}
          </fieldset>
        )}

        {available.includes('anthropic') && (
          <fieldset className="grid gap-4 rounded-[9px] border border-line p-4">
            <legend className="px-1 text-[13px] font-semibold text-ink">Anthropic (Claude)</legend>
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Модель Claude" hint={MODEL_HINT.anthropic}>
                <ModelSelect provider="anthropic" value={form.anthropic_model} models={null} onChange={(m) => set('anthropic_model', m)} />
              </Field>
              <Field label="Глубина размышлений" hint={ANTHROPIC_EFFORT_HINT}>
                <Select value={form.anthropic_effort} onChange={(e) => set('anthropic_effort', e.target.value as AnthropicEffort)}>
                  {ANTHROPIC_EFFORTS.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>
          </fieldset>
        )}

        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" variant="primary" loading={saving && !testing}>
            {ru.common.save}
          </Button>
          <Button onClick={test} loading={testing} icon={<Zap className="size-4" aria-hidden />}>
            Проверить
          </Button>
          <CheckLine result={check} />
        </div>
      </form>
    </Panel>
  )
}
