import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Sparkles } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import type { BotSettings, TemplateMeta } from '../../api/types'
import { useLoad } from '../../hooks/useLoad'
import { TemplatePicker, renderTemplate, valuesFor, withDefaults } from '../../components/TemplateBuilder'
import { cleanServices } from '../../components/ServicesEditor'
import { Button, EmptyState, ErrorState, Field, Input, Spinner, Textarea } from '../../components/ui'
import { StepHeader } from './StepHeader'

export const STORY_MIN = 10
const STORY_MAX = 6000

interface AiFillResult {
  values: Record<string, string>
  prompt: string
  missing: string[]
}

/**
 * The only bot step of the wizard: a business type, the company name and, optionally, a few words about the business
 * in the owner's own words (the bot's model lays them out over the template). The bot starts at once on a universal
 * prompt; the address, prices and the prompt itself are edited later in «Настройки бота».
 */
export function BotSetupStep({ onDone }: { onDone: (saved: boolean) => void }) {
  const templates = useLoad(() => api.get<TemplateMeta[]>('/api/templates', { silent: true }))
  const bot = useLoad(() => api.get<BotSettings>('/api/settings/bot', { silent: true }))
  const [choice, setChoice] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [story, setStory] = useState('')
  const [touched, setTouched] = useState(false)
  const [busy, setBusy] = useState<null | 'ai' | 'save'>(null)
  const [aiError, setAiError] = useState<string | null>(null)
  const initialized = useRef(false)

  useEffect(() => {
    if (initialized.current || !templates.data || !bot.data) return
    initialized.current = true
    const b = bot.data
    // the universal template first: it fits any business until the owner picks a narrower one
    const known = templates.data.find((t) => t.id === b.template_id)
    const generic = templates.data.find((t) => t.id === 'generic')
    setChoice((known ?? generic ?? templates.data[0])?.id ?? null)
    setName(b.template_values?.business_name ?? '') // the company from the Nox portal application
  }, [templates.data, bot.data])

  const template = templates.data?.find((t) => t.id === choice) ?? null
  const nameError = touched && !name.trim() ? 'Укажите название компании' : null
  const storyUsed = story.trim().length >= STORY_MIN

  async function launch(e: FormEvent) {
    e.preventDefault()
    setTouched(true)
    if (!bot.data || !template || !name.trim()) return
    setAiError(null)
    let values: Record<string, string>
    let prompt: string
    try {
      if (storyUsed) {
        setBusy('ai')
        const r = await api.post<AiFillResult>(
          `/api/templates/${encodeURIComponent(template.id)}/ai-fill`,
          { business_name: name.trim(), description: story.trim() },
          { silent: true },
        )
        values = r.values
        prompt = r.prompt
      } else {
        setBusy('save')
        values = withDefaults(template, { business_name: name.trim() })
        prompt = (await renderTemplate(template, values)).prompt
      }
    } catch (err) {
      setBusy(null)
      // the owner's text is kept: they can retry, shorten it or launch without it
      setAiError(
        err instanceof ApiError
          ? `${err.message}. Можно запустить бота без рассказа и дописать данные позже.`
          : 'Не удалось разобрать рассказ. Можно запустить бота без него и дописать данные позже.',
      )
      return
    }
    setBusy('save')
    try {
      const services = bot.data.services?.length ? bot.data.services : (template.defaults.services ?? [])
      const payload: BotSettings = {
        ...bot.data,
        ...template.defaults,
        services: cleanServices(services),
        template_id: template.id,
        template_values: valuesFor(template, values),
        prompt,
        prompt_edited: false,
        enabled: true,
      }
      await api.put<BotSettings>('/api/settings/bot', payload)
      onDone(true)
    } catch {
      /* toast */
    } finally {
      setBusy(null)
    }
  }

  const header = (
    <StepHeader
      title="Ваш бот"
      text="Выберите тип бизнеса и укажите название — бот сразу начнёт отвечать клиентам по универсальным правилам. Адрес, цены и промпт можно дописать позже в «Настройках бота»."
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
      <form onSubmit={launch} className="flex flex-col gap-6" noValidate>
        <section aria-labelledby="bs-type" className="flex flex-col gap-3">
          <h2 id="bs-type" className="text-[13px] font-semibold text-ink">
            Тип бизнеса
          </h2>
          <TemplatePicker templates={templates.data} value={choice} onChange={setChoice} />
        </section>

        <Field label="Название компании" error={nameError}>
          <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} placeholder="Например, Салон Aru Beauty" />
        </Field>

        <Field
          label="Расскажите о бизнесе своими словами — необязательно"
          hint="Адрес, часы работы, услуги и цены, как обращаться к клиентам. ИИ разложит это по шаблону; если пропустить, бот будет честно говорить, что уточнит у администратора."
        >
          <Textarea
            rows={5}
            value={story}
            maxLength={STORY_MAX}
            onChange={(e) => setStory(e.target.value)}
            placeholder="Салон на Абая 10, работаем каждый день с 10 до 20. Стрижка от 5000 ₸, маникюр 7000 ₸, окрашивание от 15000 ₸. Общаемся на «вы», тепло."
          />
        </Field>

        {aiError && (
          <p role="alert" className="rounded-[8px] border border-danger/30 bg-danger-soft px-3.5 py-2.5 text-sm text-danger">
            {aiError}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-3 border-t border-line pt-5">
          <Button
            type="submit"
            variant="primary"
            loading={busy !== null}
            icon={storyUsed ? <Sparkles className="size-4" aria-hidden /> : undefined}
          >
            {busy === 'ai' ? 'ИИ собирает промпт…' : storyUsed ? 'Собрать промпт и запустить бота' : 'Запустить бота'}
          </Button>
          {aiError && storyUsed && (
            <button type="button" onClick={() => setStory('')} className="text-[13px] text-ink-2 underline hover:text-ink">
              Запустить без рассказа
            </button>
          )}
          <button type="button" onClick={() => onDone(false)} className="text-[13px] text-ink-2 underline hover:text-ink">
            Настрою позже
          </button>
        </div>
      </form>
    </>
  )
}
