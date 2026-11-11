import { useEffect, useMemo, useRef, useState } from 'react'
import { WandSparkles } from 'lucide-react'
import { api } from '../../api/client'
import type { BotSettings, TemplateMeta } from '../../api/types'
import { useLoad } from '../../hooks/useLoad'
import { PromptEditor } from '../../components/PromptEditor'
import { OVERWRITE_CONFIRM, TemplateFields, TemplatePicker, missingRequired, renderTemplate, valuesFor } from '../../components/TemplateBuilder'
import { Button, EmptyState, ErrorState, Spinner } from '../../components/ui'
import { StepHeader } from './StepHeader'



/** Last wizard step: pick a template, fill its fields, review the generated prompt, save (the bot is enabled). */
export function BotSetupStep({ onDone }: { onDone: (saved: boolean) => void }) {
  const templates = useLoad(() => api.get<TemplateMeta[]>('/api/templates', { silent: true }))
  const bot = useLoad(() => api.get<BotSettings>('/api/settings/bot', { silent: true }))
  const [choice, setChoice] = useState<string | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [prompt, setPrompt] = useState('')
  const [edited, setEdited] = useState(false)
  const [renderedKey, setRenderedKey] = useState<string | null>(null)
  const [showErrors, setShowErrors] = useState(false)
  const [building, setBuilding] = useState(false)
  const [saving, setSaving] = useState(false)
  const promptRef = useRef<HTMLDivElement>(null)
  const initialized = useRef(false)

  useEffect(() => {
    if (initialized.current || !templates.data || !bot.data) return
    initialized.current = true
    const b = bot.data
    const known = templates.data.find((t) => t.id === b.template_id)
    setChoice(known?.id ?? templates.data[0]?.id ?? null)
    setValues(b.template_values ?? {})
    setPrompt(b.prompt ?? '')
    setEdited(Boolean(b.prompt_edited))
  }, [templates.data, bot.data])

  const template = templates.data?.find((t) => t.id === choice) ?? null
  const currentKey = useMemo(() => (template ? `${template.id}:${JSON.stringify(valuesFor(template, values))}` : null), [template, values])
  const stale = Boolean(prompt.trim() && renderedKey && renderedKey !== currentKey)

  async function build() {
    if (!template) return
    if (missingRequired(template, values).length) {
      setShowErrors(true)
      return
    }
    if (prompt.trim() && edited && !window.confirm(OVERWRITE_CONFIRM)) return
    setBuilding(true)
    try {
      const r = await renderTemplate(template, values)
      setPrompt(r.prompt)
      setEdited(false)
      setRenderedKey(currentKey)
      setShowErrors(r.missing.length > 0)
      requestAnimationFrame(() => promptRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
    } catch {
      /* toast */
    } finally {
      setBuilding(false)
    }
  }

  async function save() {
    if (!bot.data || !template || !prompt.trim()) return
    setSaving(true)
    try {
      const payload: BotSettings = {
        ...bot.data,
        ...template.defaults,
        template_id: template.id,
        template_values: valuesFor(template, values),
        prompt,
        prompt_edited: edited,
        enabled: true,
      }
      await api.put<BotSettings>('/api/settings/bot', payload)
      onDone(true)
    } catch {
      /* toast */
    } finally {
      setSaving(false)
    }
  }

  const header = (
    <StepHeader
      title="Настройка бота"
      text="Выберите тип бизнеса и заполните данные. Из них соберётся промпт — инструкция, по которой бот отвечает клиентам. Перед сохранением его можно поправить."
    />
  )

  if (templates.loading || bot.loading) return <>{header}<Spinner /></>
  const error = templates.error ?? bot.error
  if (error)
    return (
      <>
        {header}
        <ErrorState message={error.message} onRetry={() => void Promise.all([templates.reload(), bot.reload()])} />
      </>
    )
  if (!templates.data?.length || !template)
    return (
      <>
        {header}
        <EmptyState title="Шаблоны не найдены" action={<Button onClick={() => onDone(false)}>Настрою позже</Button>}>
          Промпт можно написать вручную в разделе «Настройки бота».
        </EmptyState>
      </>
    )

  return (
    <>
      {header}
      <div className="flex flex-col gap-7">
        <section aria-labelledby="bs-type" className="flex flex-col gap-3">
          <h2 id="bs-type" className="text-[13px] font-semibold text-ink">
            Тип бизнеса
          </h2>
          <TemplatePicker
            templates={templates.data}
            value={choice}
            onChange={(id) => {
              setChoice(id)
              setShowErrors(false)
            }}
          />
        </section>

        <section aria-labelledby="bs-data" className="flex flex-col gap-3">
          <h2 id="bs-data" className="text-[13px] font-semibold text-ink">
            Данные бизнеса
          </h2>
          <TemplateFields
            template={template}
            values={values}
            showErrors={showErrors}
            onChange={(k, v) => setValues((cur) => ({ ...cur, [k]: v }))}
          />
          <div className="flex flex-wrap items-center gap-3 pt-1">
            <Button
              variant={prompt.trim() && !stale ? 'secondary' : 'primary'}
              icon={<WandSparkles className="size-4" aria-hidden />}
              loading={building}
              onClick={build}
            >
              {prompt.trim() ? 'Собрать заново' : 'Собрать промпт'}
            </Button>
            {stale && <span className="text-[13px] text-warn">Данные изменились — соберите промпт заново.</span>}
          </div>
        </section>

        {prompt.trim() || renderedKey ? (
          <section ref={promptRef} className="scroll-mt-6 flex flex-col gap-3 border-t border-line pt-6">
            <PromptEditor
              label="Промпт бота"
              value={prompt}
              onChange={(v) => {
                setPrompt(v)
                setEdited(true)
              }}
              minRows={14}
              hint="Именно этот текст бот использует в разговоре. Допишите, что важно: акции, ограничения, тон."
            />
          </section>
        ) : null}

        <div className="flex flex-wrap items-center gap-3 border-t border-line pt-5">
          <Button variant="primary" loading={saving} disabled={!prompt.trim()} onClick={save}>
            Сохранить и включить бота
          </Button>
          <button type="button" onClick={() => onDone(false)} className="text-[13px] text-ink-2 underline hover:text-ink">
            Настрою позже
          </button>
        </div>
      </div>
    </>
  )
}
