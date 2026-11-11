import { useEffect, useState } from 'react'
import { Zap } from 'lucide-react'
import { api } from '../../api/client'
import type { CheckResult, LlmProvider, LlmSettings } from '../../api/types'
import { useLoad } from '../../hooks/useLoad'
import { SecretInput, secretPayload, secretState, type SecretState } from '../../components/SecretInput'
import { Button, CheckLine, ErrorState, Field, Input, Panel, Select, Spinner } from '../../components/ui'
import { toast } from '../../components/toast'
import { ru } from '../../i18n/ru'

interface Form extends Omit<LlmSettings, 'openai_key' | 'gemini_key'> {
  openai_key: SecretState
  gemini_key: SecretState
}

function toForm(s: LlmSettings): Form {
  return { ...s, openai_key: secretState(s.openai_key), gemini_key: secretState(s.gemini_key) }
}

function toPayload(f: Form): LlmSettings {
  return {
    ...f,
    openai_fallback_model: f.openai_fallback_model?.trim() || null,
    openai_key: secretPayload(f.openai_key),
    gemini_key: secretPayload(f.gemini_key),
  }
}

export function LlmSettingsPanel() {
  const { data, error, loading, reload } = useLoad(() => api.get<LlmSettings>('/api/settings/llm', { silent: true }))
  const [form, setForm] = useState<Form | null>(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [check, setCheck] = useState<CheckResult | null>(null)

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
    setTesting(true)
    setCheck(null)
    try {
      if (await save()) setCheck(await api.post<CheckResult>('/api/settings/llm/test', { provider: form?.provider }))
    } catch {
      /* toast */
    } finally {
      setTesting(false)
    }
  }

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
            </Select>
          </Field>
        </div>

        <fieldset className="grid gap-4 rounded-[9px] border border-line p-4">
          <legend className="px-1 text-[13px] font-semibold text-ink">OpenAI</legend>
          <SecretInput label="Ключ OpenAI" value={form.openai_key} onChange={(v) => set('openai_key', v)} placeholder="sk-…" />
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="Основная модель OpenAI">
              <Input value={form.openai_model} onChange={(e) => set('openai_model', e.target.value)} spellCheck={false} />
            </Field>
            <Field label="Резервная модель OpenAI" hint="Необязательно. Вызывается при ошибке основной.">
              <Input value={form.openai_fallback_model ?? ''} onChange={(e) => set('openai_fallback_model', e.target.value)} spellCheck={false} />
            </Field>
          </div>
        </fieldset>

        <fieldset className="grid gap-4 rounded-[9px] border border-line p-4">
          <legend className="px-1 text-[13px] font-semibold text-ink">Google Gemini</legend>
          <SecretInput label="Ключ Gemini" value={form.gemini_key} onChange={(v) => set('gemini_key', v)} placeholder="AIza…" />
          <Field label="Модель Gemini" className="md:max-w-[calc(50%-0.5rem)]">
            <Input value={form.gemini_model} onChange={(e) => set('gemini_model', e.target.value)} spellCheck={false} />
          </Field>
        </fieldset>

        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" variant="primary" loading={saving && !testing}>
            {ru.common.save}
          </Button>
          <Button onClick={test} loading={testing} icon={<Zap className="size-4" aria-hidden />}>
            Сохранить и проверить
          </Button>
          <CheckLine result={check} />
        </div>
      </form>
    </Panel>
  )
}
