import { useEffect, useMemo, useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { api } from '../../api/client'
import { WEEKDAYS, type AfterHoursMode, type BotSettings, type DayHours, type TemplateMeta } from '../../api/types'
import { useLoad } from '../../hooks/useLoad'
import { PageBody } from '../../components/Layout'
import { PromptEditor } from '../../components/PromptEditor'
import { Button, ErrorState, Field, Input, PageHeader, Panel, Select, Spinner, Switch, Textarea, cx } from '../../components/ui'
import { toast } from '../../components/toast'
import { ru } from '../../i18n/ru'

const SECTIONS = [
  ['profile', 'Профиль'],
  ['instructions', 'Инструкции'],
  ['services', 'Услуги и цены'],
  ['faq', 'Частые вопросы'],
  ['hours', 'Адрес и часы'],
  ['behavior', 'Поведение'],
  ['templates', 'Ответы по заявкам'],
] as const

export function BotSettingsPage() {
  const { data, error, loading, reload, setData } = useLoad(() => api.get<BotSettings>('/api/settings/bot', { silent: true }))
  const [form, setForm] = useState<BotSettings | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (data) setForm(structuredClone(data))
  }, [data])

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

  async function save() {
    if (!form) return
    setSaving(true)
    try {
      const clean: BotSettings = {
        ...form,
        services: form.services.filter((s) => s.name.trim()),
        faq: form.faq.filter((f) => f.q.trim() && f.a.trim()),
      }
      const saved = await api.put<BotSettings>('/api/settings/bot', clean)
      setData(saved && typeof saved === 'object' ? saved : clean)
      toast.success(ru.common.saved)
    } catch {
      /* toast */
    } finally {
      setSaving(false)
    }
  }

  return (
    <PageBody wide>
      <PageHeader
        title="Настройки бота"
        description="Как бот представляется, что знает о клубе и как себя ведёт. Изменения применяются к новым сообщениям сразу после сохранения."
        actions={<TemplateApply onApplied={reload} />}
      />

      <div className="grid gap-8 lg:grid-cols-[180px_minmax(0,1fr)]">
        <nav aria-label="Разделы настроек" className="hidden lg:block">
          <ul className="sticky top-6 flex flex-col gap-0.5 text-sm">
            {SECTIONS.map(([id, label]) => (
              <li key={id}>
                <a href={`#${id}`} className="block rounded-[6px] px-2.5 py-1.5 text-ink-2 no-underline hover:bg-sunken hover:text-ink">
                  {label}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <form
          className="flex min-w-0 flex-col gap-5 pb-24"
          onSubmit={(e) => {
            e.preventDefault()
            void save()
          }}
        >
          <Panel id="profile" title="Профиль" className="scroll-mt-6">
            <div className="grid gap-5">
              <Switch
                checked={form.enabled}
                onChange={(v) => set('enabled', v)}
                label="Бот отвечает клиентам"
                description="Если выключить, входящие продолжат сохраняться в «Диалогах»."
              />
              <Field label="Имя бота" hint="Так бот называет себя в переписке.">
                <Input value={form.name} onChange={(e) => set('name', e.target.value)} maxLength={80} />
              </Field>
              <Field label="Персона" hint="Тон и характер: кто он, как обращается к клиентам, насколько формально пишет.">
                <Textarea rows={3} value={form.persona} onChange={(e) => set('persona', e.target.value)} />
              </Field>
              <Field label="Приветствие" hint="Первое сообщение новому клиенту. Оставьте пустым, чтобы бот здоровался сам.">
                <Textarea rows={2} value={form.greeting} onChange={(e) => set('greeting', e.target.value)} />
              </Field>
            </div>
          </Panel>

          <Panel
            id="instructions"
            title="Инструкции"
            description="Правила для бота: что делать, о чём молчать, когда передавать оператору. Пишите простыми фразами, по одному правилу на строку."
            className="scroll-mt-6"
          >
            <PromptEditor
              label="Текст инструкций"
              value={form.instructions}
              onChange={(v) => set('instructions', v)}
              minRows={10}
              placeholder={'Например:\nОтвечай коротко, до трёх предложений.\nНе обещай скидки — предложи связаться с администратором.'}
            />
          </Panel>

          <Panel
            id="services"
            title="Услуги и цены"
            description="Бот называет цены только из этого списка."
            className="scroll-mt-6"
            actions={
              <Button size="sm" icon={<Plus className="size-3.5" aria-hidden />} onClick={() => set('services', [...form.services, { name: '', price: '', note: '' }])}>
                Добавить услугу
              </Button>
            }
          >
            {form.services.length === 0 ? (
              <p className="py-2 text-sm text-ink-2">Услуг пока нет. Добавьте хотя бы основные тарифы.</p>
            ) : (
              <div className="flex flex-col gap-2">
                <div className="hidden grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,2fr)_36px] gap-2 text-[12px] font-medium text-ink-3 md:grid">
                  <span>Название</span>
                  <span>Цена</span>
                  <span>Примечание</span>
                </div>
                {form.services.map((s, i) => (
                  <div key={i} className="grid grid-cols-2 gap-2 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,2fr)_36px]">
                    <Input aria-label={`Услуга ${i + 1}: название`} placeholder="Стандарт, 1 час" value={s.name} onChange={(e) => set('services', form.services.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} className="col-span-2 md:col-span-1" />
                    <Input aria-label={`Услуга ${i + 1}: цена`} placeholder="800 ₸" value={s.price} onChange={(e) => set('services', form.services.map((x, j) => (j === i ? { ...x, price: e.target.value } : x)))} />
                    <Input aria-label={`Услуга ${i + 1}: примечание`} placeholder="RTX 4060, 165 Гц" value={s.note} onChange={(e) => set('services', form.services.map((x, j) => (j === i ? { ...x, note: e.target.value } : x)))} />
                    <IconRemove label={`Удалить услугу ${s.name || i + 1}`} onClick={() => set('services', form.services.filter((_, j) => j !== i))} />
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel
            id="faq"
            title="Частые вопросы"
            description="Готовые ответы, которые бот использует дословно или близко к тексту."
            className="scroll-mt-6"
            actions={
              <Button size="sm" icon={<Plus className="size-3.5" aria-hidden />} onClick={() => set('faq', [...form.faq, { q: '', a: '' }])}>
                Добавить вопрос
              </Button>
            }
          >
            {form.faq.length === 0 ? (
              <p className="py-2 text-sm text-ink-2">Вопросов пока нет.</p>
            ) : (
              <div className="flex flex-col divide-y divide-line">
                {form.faq.map((f, i) => (
                  <div key={i} className="grid gap-2 py-3 first:pt-0 last:pb-0 md:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)_36px]">
                    <Input aria-label={`Вопрос ${i + 1}`} placeholder="Вопрос" value={f.q} onChange={(e) => set('faq', form.faq.map((x, j) => (j === i ? { ...x, q: e.target.value } : x)))} />
                    <Textarea aria-label={`Ответ ${i + 1}`} placeholder="Ответ" rows={2} value={f.a} onChange={(e) => set('faq', form.faq.map((x, j) => (j === i ? { ...x, a: e.target.value } : x)))} className="min-h-9" />
                    <IconRemove label={`Удалить вопрос ${i + 1}`} onClick={() => set('faq', form.faq.filter((_, j) => j !== i))} />
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel id="hours" title="Адрес и часы работы" className="scroll-mt-6">
            <div className="grid gap-5">
              <Field label="Адрес" hint="Можно добавить ориентир и как пройти.">
                <Textarea rows={2} value={form.address} onChange={(e) => set('address', e.target.value)} />
              </Field>
              <Switch
                checked={form.hours.always_open}
                onChange={(v) => set('hours', { ...form.hours, always_open: v })}
                label="Работаем круглосуточно"
              />
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
                            <Input type="time" aria-label={`${ru.weekdays[d]}: открытие`} value={h.open} disabled={h.closed} onChange={(e) => setDay({ open: e.target.value })} className="tabular w-[110px]" />
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
            </div>
          </Panel>

          <Panel id="behavior" title="Поведение" className="scroll-mt-6">
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
          </Panel>

          <Panel
            id="templates"
            title="Ответы по заявкам"
            description="Сообщения клиенту после решения администратора."
            className="scroll-mt-6"
          >
            <div className="grid gap-5">
              <PromptEditor
                label="Бронь подтверждена"
                minRows={2}
                maxLength={1000}
                value={form.booking_confirm_template}
                onChange={(v) => set('booking_confirm_template', v)}
                variables={[{ token: '{details}', title: 'Детали брони и комментарий администратора' }]}
              />
              <PromptEditor
                label="Бронь отклонена"
                minRows={2}
                maxLength={1000}
                value={form.booking_reject_template}
                onChange={(v) => set('booking_reject_template', v)}
                variables={[{ token: '{reason}', title: 'Причина, указанная администратором' }]}
              />
              <div className="grid gap-4 md:grid-cols-[200px_minmax(0,1fr)]">
                <Field label="Ждать ответа админа, мин" hint="Потом клиенту уйдёт сообщение ниже.">
                  <Input type="number" min={1} max={240} value={form.booking_request_timeout_min} onChange={(e) => set('booking_request_timeout_min', clamp(e.target.value, 1, 240, 15))} className="tabular" />
                </Field>
                <Field label="Если администратор не успел ответить">
                  <Textarea rows={2} value={form.booking_timeout_message} onChange={(e) => set('booking_timeout_message', e.target.value)} />
                </Field>
              </div>
            </div>
          </Panel>

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
      </div>
    </PageBody>
  )
}

function IconRemove({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className="grid size-9 place-items-center rounded-[7px] text-ink-3 hover:bg-danger-soft hover:text-danger"
    >
      <Trash2 className="size-4" aria-hidden />
    </button>
  )
}

/** "24:00" (end of day) is not a valid <input type=time> value, so offer it as a checkbox. */
function TimeOrMidnight({ label, value, onChange, disabled }: { label: string; value: string; onChange: (v: string) => void; disabled?: boolean }) {
  const midnight = value === '24:00'
  return (
    <span className="flex items-center gap-2">
      {!midnight && <Input type="time" aria-label={label} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} className="tabular w-[110px]" />}
      <label className="flex items-center gap-1.5 text-[13px] text-ink-2">
        <input type="checkbox" checked={midnight} disabled={disabled} onChange={(e) => onChange(e.target.checked ? '24:00' : '23:00')} className="accent-[var(--color-accent)]" />
        до конца дня
      </label>
    </span>
  )
}

function TemplateApply({ onApplied }: { onApplied: () => void }) {
  const list = useLoad(() => api.get<TemplateMeta[]>('/api/templates', { silent: true }))
  const [choice, setChoice] = useState('')
  const [busy, setBusy] = useState(false)
  if (!list.data?.length) return null

  async function apply() {
    const t = list.data?.find((x) => x.id === choice)
    if (!t) return
    if (!window.confirm(`Применить шаблон «${t.name}»? Текущие настройки бота будут заменены.`)) return
    setBusy(true)
    try {
      await api.post(`/api/templates/${encodeURIComponent(t.id)}/apply`)
      toast.success(`Шаблон «${t.name}» применён`)
      onApplied()
      setChoice('')
    } catch {
      /* toast */
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex items-center gap-2">
      <label htmlFor="tpl" className="sr-only">
        Шаблон
      </label>
      <Select id="tpl" value={choice} onChange={(e) => setChoice(e.target.value)} className="h-8 w-auto text-[13px]">
        <option value="">Шаблон…</option>
        {list.data.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name}
          </option>
        ))}
      </Select>
      <Button size="sm" onClick={apply} disabled={!choice} loading={busy}>
        Применить
      </Button>
    </div>
  )
}
