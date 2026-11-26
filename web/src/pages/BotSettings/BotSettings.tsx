import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import { ChevronDown, CircleAlert, PenLine, WandSparkles } from 'lucide-react'
import { api } from '../../api/client'
import { WEEKDAYS, type AfterHoursMode, type BotSettings, type DayHours, type TemplateMeta } from '../../api/types'
import { useLoad } from '../../hooks/useLoad'
import { PageBody } from '../../components/Layout'
import { PromptEditor } from '../../components/PromptEditor'
import { OVERWRITE_CONFIRM, TemplateFields, TemplatePicker, missingRequired, renderTemplate, valuesFor } from '../../components/TemplateBuilder'
import { Badge, Button, ErrorState, Field, Input, PageHeader, Panel, Select, Spinner, Switch, Textarea, cx } from '../../components/ui'
import { toast } from '../../components/toast'
import { ru } from '../../i18n/ru'


export function BotSettingsPage() {
  const { data, error, loading, reload, setData } = useLoad(() => api.get<BotSettings>('/api/settings/bot', { silent: true }))
  const templates = useLoad(() => api.get<TemplateMeta[]>('/api/templates', { silent: true }))
  const [form, setForm] = useState<BotSettings | null>(null)
  const [choice, setChoice] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [building, setBuilding] = useState(false)
  const [showErrors, setShowErrors] = useState(false)
  const promptRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (data) setForm(structuredClone(data))
  }, [data])

  // Picker follows the stored template; falls back to the first one when none was used yet.
  useEffect(() => {
    if (!templates.data?.length || !data) return
    setChoice((cur) => cur ?? (templates.data!.some((t) => t.id === data.template_id) ? data.template_id : templates.data![0].id))
  }, [templates.data, data])

  const dirty = useMemo(() => !!form && !!data && JSON.stringify(form) !== JSON.stringify(data), [form, data])

  useEffect(() => {
    if (!dirty) return
    const h = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', h)
    return () => window.removeEventListener('beforeunload', h)
  }, [dirty])

  if (loading && !form) return <PageBody><Spinner /></PageBody>
  if (error && !form) return <PageBody><ErrorState message={error.message} onRetry={reload} /></PageBody>
  if (!form) return null

  const set = <K extends keyof BotSettings>(k: K, v: BotSettings[K]) => setForm((f) => (f ? { ...f, [k]: v } : f))
  const clamp = (v: string, min: number, max: number, fallback: number) => {
    const n = Number.parseInt(v, 10)
    return Number.isNaN(n) ? fallback : Math.min(max, Math.max(min, n))
  }
  const template = templates.data?.find((t) => t.id === choice) ?? null
  const usedTemplate = templates.data?.find((t) => t.id === form.template_id) ?? null

  async function build() {
    if (!form || !template) return
    if (missingRequired(template, form.template_values).length) {
      setShowErrors(true)
      return
    }
    if (form.prompt.trim() && form.prompt_edited && !window.confirm(OVERWRITE_CONFIRM)) return
    setBuilding(true)
    try {
      const values = valuesFor(template, form.template_values)
      const r = await renderTemplate(template, values)
      setForm((f) => (f ? { ...f, prompt: r.prompt, prompt_edited: false, template_id: template.id, template_values: values } : f))
      setShowErrors(r.missing.length > 0)
      toast.success('Промпт собран. Проверьте его и сохраните.')
      requestAnimationFrame(() => promptRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
    } catch {
      /* toast */
    } finally {
      setBuilding(false)
    }
  }

  async function save() {
    if (!form) return
    setSaving(true)
    try {
      const saved = await api.put<BotSettings>('/api/settings/bot', form)
      setData(saved && typeof saved === 'object' ? saved : form)
      toast.success(ru.common.saved)
    } catch {
      /* toast */
    } finally {
      setSaving(false)
    }
  }

  const promptEmpty = !form.prompt.trim()

  return (
    <PageBody wide>
      <PageHeader
        title="Настройки бота"
        description="Промпт — главное: из него бот знает всё о вашем бизнесе. Изменения применяются к новым сообщениям сразу после сохранения."
      />

      <form
        className="mx-auto flex max-w-[880px] min-w-0 flex-col gap-5 pb-24"
        onSubmit={(e) => {
          e.preventDefault()
          void save()
        }}
      >
        {promptEmpty && (
          <div role="status" className="flex items-start gap-3 rounded-[10px] border border-warn/40 bg-warn-soft px-4 py-3 text-sm text-ink">
            <CircleAlert className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
            <p>
              Бот не отвечает клиентам, пока промпт пуст. Выберите шаблон, заполните данные и нажмите «Собрать промпт из шаблона» — или
              напишите промпт сами.
            </p>
          </div>
        )}

        <Panel
          id="template"
          title="Шаблон"
          description="Тип бизнеса и его данные. Промпт собирается из них, но живёт отдельно: шаблон не меняет его, пока вы не соберёте заново."
          className="scroll-mt-6"
        >
          {templates.loading ? (
            <Spinner />
          ) : templates.error ? (
            <ErrorState message={templates.error.message} onRetry={templates.reload} />
          ) : !templates.data?.length || !template ? (
            <p className="py-2 text-sm text-ink-2">Шаблоны не найдены. Промпт можно написать вручную ниже.</p>
          ) : (
            <div className="flex flex-col gap-6">
              <TemplatePicker
                templates={templates.data}
                value={choice}
                onChange={(id) => {
                  setChoice(id)
                  setShowErrors(false)
                }}
              />
              <TemplateFields
                template={template}
                values={form.template_values}
                showErrors={showErrors}
                onChange={(k, v) => set('template_values', { ...form.template_values, [k]: v })}
              />
              <div className="flex flex-wrap items-center gap-3 border-t border-line pt-4">
                <Button icon={<WandSparkles className="size-4" aria-hidden />} loading={building} onClick={build} variant={promptEmpty ? 'primary' : 'secondary'}>
                  Собрать промпт из шаблона
                </Button>
                <span className="text-[13px] text-ink-2">
                  {promptEmpty ? 'Готовый текст появится ниже.' : 'Текущий промпт будет заменён.'}
                </span>
              </div>
            </div>
          )}
        </Panel>

        <div ref={promptRef} className="scroll-mt-6">
          <Panel
            id="prompt"
            title="Промпт бота"
            description="Именно этот текст бот использует в разговоре: кто он, что знает о бизнесе и как отвечает."
            actions={
              form.prompt_edited ? (
                <Badge tone="warn">
                  <PenLine className="size-3" aria-hidden /> Изменён вручную
                </Badge>
              ) : usedTemplate && !promptEmpty ? (
                <Badge>Из шаблона «{usedTemplate.name}»</Badge>
              ) : undefined
            }
          >
            <PromptEditor
              label="Текст промпта"
              value={form.prompt}
              onChange={(v) => setForm((f) => (f ? { ...f, prompt: v, prompt_edited: true } : f))}
              minRows={18}
              maxLength={20000}
              placeholder={'Ты — администратор «…». Адрес: …\nУслуги и цены:\n…'}
              hint="Пишите обычным языком. Цены, адреса и правила бот берёт только отсюда."
            />
          </Panel>
        </div>

        <Collapsible
          id="behavior"
          title="Поведение"
          description="Имя, приветствие, часы работы, паузы и ответы по заявкам."
        >
          <div className="flex flex-col divide-y divide-line">
            <div className="grid gap-5 pb-6">
              <Switch
                checked={form.enabled}
                onChange={(v) => set('enabled', v)}
                label="Бот отвечает клиентам"
                description="Если выключить, входящие продолжат сохраняться в «Диалогах»."
              />
              <div className="grid gap-5 md:grid-cols-2">
                <Field label="Имя бота" hint="Так бот называет себя в переписке.">
                  <Input value={form.name} onChange={(e) => set('name', e.target.value)} maxLength={80} />
                </Field>
                <Field label="Приветствие" hint="Первое сообщение новому клиенту. Пусто — бот здоровается сам.">
                  <Textarea rows={2} value={form.greeting} onChange={(e) => set('greeting', e.target.value)} className="min-h-9" />
                </Field>
              </div>
            </div>

            <SubSection title="Часы работы">
              <Switch checked={form.hours.always_open} onChange={(v) => set('hours', { ...form.hours, always_open: v })} label="Работаем круглосуточно" />
              {!form.hours.always_open && (
                <fieldset className="rounded-[9px] border border-line">
                  <legend className="sr-only">Часы по дням недели</legend>
                  <ul className="divide-y divide-line">
                    {WEEKDAYS.map((d) => {
                      const h = form.hours[d]
                      const setDay = (patch: Partial<DayHours>) => set('hours', { ...form.hours, [d]: { ...h, ...patch } })
                      return (
                        <li key={d} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5">
                          <span className="w-28 text-sm text-ink">{ru.weekdays[d]}</span>
                          <label className="flex items-center gap-2 text-[13px] text-ink-2">
                            <input type="checkbox" checked={h.closed} onChange={(e) => setDay({ closed: e.target.checked })} className="accent-[var(--color-accent)]" />
                            Выходной
                          </label>
                          <div className={cx('flex items-center gap-2', h.closed && 'opacity-40')}>
                            <Input type="time" required aria-label={`${ru.weekdays[d]}: открытие`} value={h.open} disabled={h.closed} onChange={(e) => e.target.value && setDay({ open: e.target.value })} className="tabular w-[110px]" />
                            <span className="text-ink-3" aria-hidden>—</span>
                            <TimeOrMidnight label={`${ru.weekdays[d]}: закрытие`} value={h.close} disabled={h.closed} onChange={(v) => setDay({ close: v })} />
                          </div>
                        </li>
                      )
                    })}
                  </ul>
                </fieldset>
              )}
              <div className="grid gap-4 md:grid-cols-2">
                <Field label="Вне рабочего времени">
                  <Select value={form.after_hours_mode} onChange={(e) => set('after_hours_mode', e.target.value as AfterHoursMode)}>
                    <option value="reply">Отвечать как обычно</option>
                    <option value="fixed_message">Отправлять готовое сообщение</option>
                    <option value="silent">Не отвечать</option>
                  </Select>
                </Field>
                {form.after_hours_mode === 'fixed_message' && (
                  <Field label="Сообщение вне рабочего времени" className="md:col-span-2">
                    <Textarea rows={2} value={form.after_hours_message} onChange={(e) => set('after_hours_message', e.target.value)} placeholder="Мы откроемся в 10:00 и сразу ответим." />
                  </Field>
                )}
              </div>
            </SubSection>

            <SubSection title="Темп и память">
              <div className="grid gap-5 md:grid-cols-3">
                <Field label="Задержка склейки, сек" hint="Бот ждёт, пока клиент допишет несколько сообщений подряд. От 3 до 120.">
                  <Input type="number" min={3} max={120} value={form.buffer_delay_seconds} onChange={(e) => set('buffer_delay_seconds', clamp(e.target.value, 3, 120, 8))} className="tabular" />
                </Field>
                <Field label="Пауза после оператора, мин" hint="Сколько бот молчит после ответа человека в диалоге.">
                  <Input type="number" min={1} max={1440} value={form.handover_pause_minutes} onChange={(e) => set('handover_pause_minutes', clamp(e.target.value, 1, 1440, 60))} className="tabular" />
                </Field>
                <Field label="Память, сообщений" hint="Сколько последних сообщений бот учитывает в ответе.">
                  <Input type="number" min={2} max={100} value={form.memory_messages} onChange={(e) => set('memory_messages', clamp(e.target.value, 2, 100, 20))} className="tabular" />
                </Field>
              </div>
            </SubSection>

            <SubSection title="Ответы по заявкам" description="Сообщения клиенту после решения администратора.">
              <PromptEditor
                label="Заявка подтверждена"
                minRows={2}
                maxLength={1000}
                value={form.booking_confirm_template}
                onChange={(v) => set('booking_confirm_template', v)}
                variables={[{ token: '{details}', title: 'Детали заявки и комментарий администратора' }]}
              />
              <PromptEditor
                label="Заявка отклонена"
                minRows={2}
                maxLength={1000}
                value={form.booking_reject_template}
                onChange={(v) => set('booking_reject_template', v)}
                variables={[{ token: '{reason}', title: 'Причина, указанная администратором' }]}
              />
              <div className="grid gap-4 md:grid-cols-[200px_minmax(0,1fr)]">
                <Field label="Ждать ответа админа, мин" hint="Потом клиенту уйдёт сообщение справа.">
                  <Input type="number" min={1} max={240} value={form.booking_request_timeout_min} onChange={(e) => set('booking_request_timeout_min', clamp(e.target.value, 1, 240, 15))} className="tabular" />
                </Field>
                <Field label="Если администратор не успел ответить">
                  <Textarea rows={2} value={form.booking_timeout_message} onChange={(e) => set('booking_timeout_message', e.target.value)} />
                </Field>
              </div>
            </SubSection>

            <SubSection title="Когда закончились коины" description="Бот не может ответить сам: клиент один раз получает это сообщение, диалог переходит к оператору.">
              <Field label="Сообщение клиенту">
                <Textarea
                  rows={2}
                  maxLength={1000}
                  value={form.no_coins_message}
                  onChange={(e) => set('no_coins_message', e.target.value)}
                  placeholder="Спасибо за сообщение! Сейчас подключится менеджер и ответит вам."
                />
              </Field>
            </SubSection>
          </div>
        </Collapsible>

        <div
          className={cx(
            'sticky bottom-4 z-10 flex flex-wrap items-center justify-between gap-3 rounded-[10px] border px-4 py-3 transition-[opacity,transform] duration-200 ease-out',
            dirty ? 'border-line-strong bg-surface shadow-[var(--shadow-pop)]' : 'pointer-events-none translate-y-2 border-transparent opacity-0',
          )}
          aria-hidden={!dirty}
        >
          <span className="text-sm text-ink-2">{ru.common.unsaved}</span>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => data && setForm(structuredClone(data))} tabIndex={dirty ? 0 : -1}>
              {ru.common.discard}
            </Button>
            <Button type="submit" variant="primary" loading={saving} tabIndex={dirty ? 0 : -1}>
              {ru.common.save}
            </Button>
          </div>
        </div>
      </form>
    </PageBody>
  )
}

function SubSection({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <div className="grid gap-4 py-6 last:pb-0">
      <div>
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        {description && <p className="mt-0.5 text-[13px] text-ink-2">{description}</p>}
      </div>
      {children}
    </div>
  )
}

/** Panel whose body can be folded away; technical settings stay out of the way until needed. */
function Collapsible({ id, title, description, children }: { id: string; title: string; description: string; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const bodyId = useId()
  return (
    <section id={id} className="scroll-mt-6 rounded-[var(--radius-panel)] border border-line bg-surface">
      <h2>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-center justify-between gap-3 rounded-[var(--radius-panel)] px-5 py-4 text-left hover:bg-sunken/60"
        >
          <span className="min-w-0">
            <span className="block text-[15px] font-semibold text-ink">{title}</span>
            <span className="mt-0.5 block text-[13px] font-normal text-ink-2">{description}</span>
          </span>
          <ChevronDown className={cx('size-4 shrink-0 text-ink-3 transition-transform duration-200 ease-out', open && 'rotate-180')} aria-hidden />
        </button>
      </h2>
      {/* Mounted only when open: auto-growing editors cannot measure themselves inside a hidden subtree. */}
      {open && (
        <div id={bodyId} className="border-t border-line px-5 py-5">
          {children}
        </div>
      )}
    </section>
  )
}

/** "24:00" (end of day) is not a valid <input type=time> value, so offer it as a checkbox. */
function TimeOrMidnight({ label, value, onChange, disabled }: { label: string; value: string; onChange: (v: string) => void; disabled?: boolean }) {
  const midnight = value === '24:00'
  return (
    <span className="flex items-center gap-2">
      {!midnight && <Input type="time" required aria-label={label} value={value} disabled={disabled} onChange={(e) => e.target.value && onChange(e.target.value)} className="tabular w-[110px]" />}
      <label className="flex items-center gap-1.5 text-[13px] text-ink-2">
        <input type="checkbox" checked={midnight} disabled={disabled} onChange={(e) => onChange(e.target.checked ? '24:00' : '23:00')} className="accent-[var(--color-accent)]" />
        до конца дня
      </label>
    </span>
  )
}
