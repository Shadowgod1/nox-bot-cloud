import { useEffect, useState } from 'react'
import { Zap } from 'lucide-react'
import { api } from '../../api/client'
import type { AnthropicEffort, LlmProvider, LlmSettings, LlmTestResult } from '../../api/types'
import { useLoad } from '../../hooks/useLoad'
import { SecretInput, secretPayload, secretState, type SecretState } from '../../components/SecretInput'
import { Button, CheckLine, type CheckView, ErrorState, Field, Input, Panel, Select, Spinner } from '../../components/ui'
import { toast } from '../../components/toast'
import { ru } from '../../i18n/ru'

import { ANTHROPIC_DEFAULT_MODEL, ANTHROPIC_EFFORT_HINT, ANTHROPIC_EFFORTS } from './anthropic'
import { MODEL_HINT, ModelSelect, type LlmModelsResult } from './models'

interface Form extends Omit<LlmSettings, 'openai_key' | 'gemini_key' | 'anthropic_key'> {
  openai_key: SecretState
  gemini_key: SecretState
  anthropic_key: SecretState
}

function toForm(s: LlmSettings): Form {
  return {
    ...s,
    anthropic_model: s.anthropic_model ?? ANTHROPIC_DEFAULT_MODEL,
    anthropic_effort: s.anthropic_effort ?? 'low',
    openai_key: secretState(s.openai_key),
    gemini_key: secretState(s.gemini_key),
    anthropic_key: secretState(s.anthropic_key ?? null),
  }
}

function toPayload(f: Form): LlmSettings {
  return {
    ...f,
    openai_fallback_model: f.openai_fallback_model?.trim() || null,
    anthropic_model: f.anthropic_model.trim() || ANTHROPIC_DEFAULT_MODEL,
    openai_key: secretPayload(f.openai_key),
    gemini_key: secretPayload(f.gemini_key),
    anthropic_key: secretPayload(f.anthropic_key),
  }
}

export function LlmSettingsPanel() {
  const { data, error, loading, reload } = useLoad(() => api.get<LlmSettings>('/api/settings/llm', { silent: true }))
  const [form, setForm] = useState<Form | null>(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [check, setCheck] = useState<CheckView | null>(null)
  const [models, setModels] = useState<Record<'openai' | 'gemini', string[] | null>>({ openai: null, gemini: null })
  const [listing, setListing] = useState<'openai' | 'gemini' | null>(null)

  useEffect(() => {
    if (data) setForm(toForm(data))
  }, [data])

  if (loading && !form) return <Panel title="Языковая модель"><Spinner /></Panel>
  if (error && !form) return <Panel title="Языковая модель"><ErrorState message={error.message} onRetry={reload} /></Panel>
  if (!form) return null

  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => (f ? { ...f, [k]: v } : f))

  async function save(): Promise<boolean> {
    if (!form) return false
    setSaving(true)
    try {
      const saved = await api.put<LlmSettings>('/api/settings/llm', toPayload(form))
      setForm(toForm(saved ?? toPayload(form)))
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
      // Core checks the unsaved form values (masked keys fall back to the stored ones); nothing is saved.
      const r = await api.post<LlmTestResult>('/api/settings/llm/test', toPayload(form))
      setCheck({ ok: r.ok, message: r.ok ? undefined : r.error })
    } catch {
      /* toast */
    } finally {
      setTesting(false)
    }
  }

  async function loadModels(provider: 'openai' | 'gemini') {
    if (!form) return
    setListing(provider)
    try {
      // the provider's model list for the (unsaved) key; listing validates the key without spending tokens
      const r = await api.post<LlmModelsResult>('/api/settings/llm/models', { ...toPayload(form), provider })
      if (r.ok) setModels((m) => ({ ...m, [provider]: r.models }))
      else toast.error(r.error ?? 'Не удалось получить список моделей')
    } catch {
      /* toast */
    } finally {
      setListing(null)
    }
  }

  const loadButton = (provider: 'openai' | 'gemini') => (
    <button
      type="button"
      onClick={() => void loadModels(provider)}
      disabled={listing !== null}
      className="self-start text-[13px] text-ink-2 underline hover:text-ink disabled:opacity-50"
    >
      {listing === provider ? 'Загружаем…' : models[provider] ? 'Обновить список моделей' : 'Загрузить список моделей'}
    </button>
  )

  return (
    <Panel
      title="Языковая модель"
      description="Провайдер, модели и ключи API. Ключи хранятся зашифрованными и показываются только последними символами."
    >
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
              <option value="openai">OpenAI</option>
              <option value="gemini">Google Gemini</option>
              <option value="anthropic">Anthropic (Claude)</option>
            </Select>
          </Field>
          <Field label="Аварийный провайдер" hint="Используется, если основной недоступен. Нужен второй ключ.">
            <Select
              value={form.emergency_provider ?? ''}
              onChange={(e) => set('emergency_provider', (e.target.value || null) as LlmProvider | null)}
            >
              <option value="">Не использовать</option>
              <option value="openai">OpenAI</option>
              <option value="gemini">Google Gemini</option>
              <option value="anthropic">Anthropic (Claude)</option>
            </Select>
          </Field>
        </div>

        <fieldset className="grid gap-4 rounded-[9px] border border-line p-4">
          <legend className="px-1 text-[13px] font-semibold text-ink">OpenAI</legend>
          <SecretInput label="Ключ OpenAI" value={form.openai_key} onChange={(v) => set('openai_key', v)} placeholder="sk-…" />
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

        <fieldset className="grid gap-4 rounded-[9px] border border-line p-4">
          <legend className="px-1 text-[13px] font-semibold text-ink">Google Gemini</legend>
          <SecretInput label="Ключ Gemini" value={form.gemini_key} onChange={(v) => set('gemini_key', v)} placeholder="AIza…" />
          <Field label="Модель Gemini" hint={MODEL_HINT.gemini} className="md:max-w-[calc(50%-0.5rem)]">
            <ModelSelect provider="gemini" value={form.gemini_model} models={models.gemini} onChange={(m) => set('gemini_model', m)} />
          </Field>
          {loadButton('gemini')}
        </fieldset>

        <fieldset className="grid gap-4 rounded-[9px] border border-line p-4">
          <legend className="px-1 text-[13px] font-semibold text-ink">Anthropic (Claude)</legend>
          <SecretInput
            label="Ключ Anthropic"
            value={form.anthropic_key}
            onChange={(v) => set('anthropic_key', v)}
            placeholder="sk-ant-…"
          />
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="Модель Claude" hint={MODEL_HINT.anthropic}>
              <ModelSelect provider="anthropic" value={form.anthropic_model} models={null} onChange={(m) => set('anthropic_model', m)} />
            </Field>
            <Field label="Глубина размышлений" hint={ANTHROPIC_EFFORT_HINT}>
              <Select
                value={form.anthropic_effort}
                onChange={(e) => set('anthropic_effort', e.target.value as AnthropicEffort)}
              >
                {ANTHROPIC_EFFORTS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
        </fieldset>

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
