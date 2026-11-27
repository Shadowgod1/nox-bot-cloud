import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Check, UserPlus, Users } from 'lucide-react'
import { api } from '../api/client'
import { WEEKDAYS, type BotSettings, type DayHours, type WeeklyHours } from '../api/types'
import { useLoad } from '../hooks/useLoad'
import { plural, ru } from '../i18n/ru'
import { PhoneListInput } from './PhoneListInput'
import { ErrorState, Field, Input, Spinner, Select, Textarea, cx } from './ui'

/** The reply-rule part of the bot settings (the rest of BotSettings is untouched by these forms). */
export type ReplyRulesValue = Pick<
  BotSettings,
  | 'new_contacts_only'
  | 'exclude_phones'
  | 'hours'
  | 'work_hours_mode'
  | 'after_hours_mode'
  | 'after_hours_message'
  | 'timezone'
  | 'handover_pause_minutes'
>

export type Schedule = 'always' | 'off_hours' | 'work_hours' | 'work_hours_message'

/** One choice in the UI maps onto two settings: inside working hours and outside them. */
export function scheduleOf(v: Pick<BotSettings, 'work_hours_mode' | 'after_hours_mode'>): Schedule {
  if (v.work_hours_mode === 'silent') return 'off_hours'
  if (v.after_hours_mode === 'silent') return 'work_hours'
  if (v.after_hours_mode === 'fixed_message') return 'work_hours_message'
  return 'always'
}

export function applySchedule<T extends ReplyRulesValue>(v: T, s: Schedule): T {
  const patch: Partial<ReplyRulesValue> =
    s === 'always'
      ? { work_hours_mode: 'reply', after_hours_mode: 'reply' }
      : s === 'off_hours'
        ? { work_hours_mode: 'silent', after_hours_mode: 'reply' }
        : s === 'work_hours'
          ? { work_hours_mode: 'reply', after_hours_mode: 'silent' }
          : { work_hours_mode: 'reply', after_hours_mode: 'fixed_message' }
  // a schedule needs working hours: "round the clock" would make the choice meaningless
  const hours = s !== 'always' && v.hours.always_open ? { ...v.hours, always_open: false } : v.hours
  return { ...v, ...patch, hours }
}

const SCHEDULES: [Schedule, string, string][] = [
  ['always', 'Всегда', 'Бот отвечает круглосуточно.'],
  ['off_hours', 'Только в нерабочее время', 'Днём отвечают администраторы, бот подхватывает вечером, ночью и в выходные.'],
  ['work_hours', 'Только в рабочее время', 'В нерабочее время бот молчит, сообщения ждут вас в «Диалогах».'],
  ['work_hours_message', 'В рабочее время, ночью — короткий ответ', 'Вне графика клиент получает ваш текст, например «Ответим утром».'],
]

export const TIMEZONES: [string, string][] = [
  ['Asia/Almaty', 'Казахстан (UTC+5)'],
  ['Asia/Tashkent', 'Узбекистан (UTC+5)'],
  ['Asia/Bishkek', 'Кыргызстан (UTC+6)'],
  ['Asia/Novosibirsk', 'Новосибирск (UTC+7)'],
  ['Europe/Moscow', 'Москва (UTC+3)'],
]

const PAUSE_HOURS = [1, 2, 4, 8, 12, 24, 48, 72]

/** 60 -> "1 час", 240 -> "4 часа", 1440 -> "сутки", 2880 -> "48 часов", 4320 -> "72 часа". */
export function pauseLabel(minutes: number) {
  if (minutes % 60) return `${minutes} мин`
  const h = minutes / 60
  return h === 24 ? 'сутки' : `${h} ${plural(h, ['час', 'часа', 'часов'])}`
}

/** Controlled fields; used inside the bot settings form and by the self-saving card of the setup wizard. */
export function ReplyRulesFields<T extends ReplyRulesValue>({
  value,
  onChange,
  knownCount,
  compact,
}: {
  value: T
  onChange: (v: T) => void
  knownCount?: number | null
  compact?: boolean
}) {
  const set = <K extends keyof ReplyRulesValue>(k: K, v: ReplyRulesValue[K]) => onChange({ ...value, [k]: v })
  const schedule = scheduleOf(value)
  const pauseOptions = PAUSE_HOURS.map((h) => h * 60)
  if (!pauseOptions.includes(value.handover_pause_minutes)) pauseOptions.unshift(value.handover_pause_minutes)

  return (
    <div className="flex flex-col divide-y divide-line">
      <Block title="Кому отвечает бот" first>
        <div role="radiogroup" aria-label="Кому отвечает бот" className="grid gap-2.5 sm:grid-cols-2">
          <Choice
            checked={!value.new_contacts_only}
            onSelect={() => set('new_contacts_only', false)}
            icon={<Users className="size-4" aria-hidden />}
            title="Всем, кто напишет"
            text="И новым клиентам, и тем, кто писал раньше."
          />
          <Choice
            checked={value.new_contacts_only}
            onSelect={() => set('new_contacts_only', true)}
            icon={<UserPlus className="size-4" aria-hidden />}
            title="Только новым контактам"
            text="Старых клиентов, друзей и родных бот не трогает."
          />
        </div>
        <p className="max-w-[80ch] text-[13px] leading-relaxed text-ink-2">
          {value.new_contacts_only ? 'Как бот понимает, кто новый: ' : 'Если выберете «только новым»: '}
          при подключении WhatsApp передаёт боту недавнюю переписку, и бот запоминает всех, кто уже писал на этот номер. Ещё он
          запоминает тех, кому вы первыми написали с телефона. Им бот не отвечает — вы общаетесь с ними сами.
          {typeof knownCount === 'number' && knownCount > 0 && <> Сейчас запомнено контактов: <span className="tabular font-medium text-ink">{knownCount}</span>.</>}
          {' '}Если кого-то не узнал — добавьте номер в список ниже.
        </p>
        <PhoneListInput
          label="Не отвечать этим номерам"
          hint="Можно вставить сразу столбец номеров из Excel или заметок — каждый с новой строки."
          value={value.exclude_phones}
          onChange={(v) => set('exclude_phones', v)}
          emptyText="Бот отвечает всем номерам."
        />
      </Block>

      <Block title="Когда отвечает">
        <div role="radiogroup" aria-label="Когда отвечает бот" className={cx('grid gap-2', !compact && 'md:grid-cols-2')}>
          {SCHEDULES.map(([key, title, text]) => (
            <Choice key={key} checked={schedule === key} onSelect={() => onChange(applySchedule(value, key))} title={title} text={text} />
          ))}
        </div>
        {schedule !== 'always' && (
          <>
            <WeekHoursEditor hours={value.hours} onChange={(h) => set('hours', h)} />
            <Field label="Часовой пояс" hint="По нему бот сверяет время с графиком." className="max-w-xs">
              <Select value={value.timezone || 'Asia/Almaty'} onChange={(e) => set('timezone', e.target.value)}>
                {(TIMEZONES.some(([z]) => z === value.timezone) || !value.timezone ? TIMEZONES : [[value.timezone, value.timezone] as [string, string], ...TIMEZONES]).map(
                  ([zone, label]) => (
                    <option key={zone} value={zone}>
                      {label}
                    </option>
                  ),
                )}
              </Select>
            </Field>
          </>
        )}
        {schedule === 'work_hours_message' && (
          <Field label="Ответ в нерабочее время">
            <Textarea
              rows={2}
              value={value.after_hours_message}
              onChange={(e) => set('after_hours_message', e.target.value)}
              placeholder="Здравствуйте! Мы откроемся в 10:00 и сразу ответим."
            />
          </Field>
        )}
      </Block>

      <Block title="Если администратор ответил сам">
        <p className="max-w-[80ch] text-[13px] leading-relaxed text-ink-2">
          Напишите клиенту с телефона или из «Диалогов» — бот сразу замолчит в этом чате. Вернуть его можно кнопкой «Вернуть бота» в
          переписке, иначе он вернётся сам через выбранное время.
        </p>
        <Field label="Бот молчит после ответа человека" className="max-w-xs">
          <Select value={value.handover_pause_minutes} onChange={(e) => set('handover_pause_minutes', Number(e.target.value))}>
            {pauseOptions.map((m) => (
              <option key={m} value={m}>
                {pauseLabel(m)}
              </option>
            ))}
          </Select>
        </Field>
      </Block>
    </div>
  )
}

function Block({ title, children, first }: { title: string; children: ReactNode; first?: boolean }) {
  return (
    <section className={cx('grid gap-3 py-5 last:pb-0', first && 'pt-0')}>
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      {children}
    </section>
  )
}

function Choice({ checked, onSelect, title, text, icon }: { checked: boolean; onSelect: () => void; title: string; text: string; icon?: ReactNode }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={checked}
      onClick={onSelect}
      className={cx(
        'flex items-start gap-3 rounded-[9px] border p-3 text-left transition-colors',
        checked ? 'border-accent bg-accent-soft' : 'border-line hover:border-line-strong',
      )}
    >
      <span
        aria-hidden
        className={cx('mt-0.5 grid size-4 shrink-0 place-items-center rounded-full border', checked ? 'border-accent bg-accent-solid text-white' : 'border-line-strong')}
      >
        {checked && <Check className="size-3" />}
      </span>
      <span className="min-w-0">
        <span className="flex items-center gap-1.5 text-sm font-medium text-ink">
          {icon && <span className="text-ink-3">{icon}</span>}
          {title}
        </span>
        <span className="mt-0.5 block text-[13px] text-ink-2">{text}</span>
      </span>
    </button>
  )
}

/** Working hours by weekday. "24:00" (end of day) is offered as a checkbox: <input type=time> cannot hold it. */
export function WeekHoursEditor({ hours, onChange }: { hours: WeeklyHours; onChange: (h: WeeklyHours) => void }) {
  return (
    <fieldset className="rounded-[9px] border border-line">
      <legend className="sr-only">Рабочие часы по дням недели</legend>
      <ul className="divide-y divide-line">
        {WEEKDAYS.map((d) => {
          const h = hours[d]
          const setDay = (patch: Partial<DayHours>) => onChange({ ...hours, always_open: false, [d]: { ...h, ...patch } })
          return (
            <li key={d} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2.5 sm:px-4">
              <span className="w-24 text-sm text-ink sm:w-28">{ru.weekdays[d]}</span>
              <label className="flex items-center gap-2 text-[13px] text-ink-2">
                <input type="checkbox" checked={h.closed} onChange={(e) => setDay({ closed: e.target.checked })} className="accent-[var(--color-accent)]" />
                Выходной
              </label>
              <div className={cx('flex flex-wrap items-center gap-2', h.closed && 'opacity-40')}>
                <Input
                  type="time"
                  required
                  aria-label={`${ru.weekdays[d]}: открытие`}
                  value={h.open}
                  disabled={h.closed}
                  onChange={(e) => e.target.value && setDay({ open: e.target.value })}
                  className="tabular w-[110px]"
                />
                <span className="text-ink-3" aria-hidden>
                  —
                </span>
                <TimeOrMidnight label={`${ru.weekdays[d]}: закрытие`} value={h.close} disabled={h.closed} onChange={(v) => setDay({ close: v })} />
              </div>
            </li>
          )
        })}
      </ul>
    </fieldset>
  )
}

function TimeOrMidnight({ label, value, onChange, disabled }: { label: string; value: string; onChange: (v: string) => void; disabled?: boolean }) {
  const midnight = value === '24:00'
  return (
    <span className="flex items-center gap-2">
      {!midnight && (
        <Input type="time" required aria-label={label} value={value} disabled={disabled} onChange={(e) => e.target.value && onChange(e.target.value)} className="tabular w-[110px]" />
      )}
      <label className="flex items-center gap-1.5 text-[13px] text-ink-2">
        <input type="checkbox" checked={midnight} disabled={disabled} onChange={(e) => onChange(e.target.checked ? '24:00' : '23:00')} className="accent-[var(--color-accent)]" />
        до конца дня
      </label>
    </span>
  )
}

export function useKnownCount() {
  const known = useLoad(() => api.get<{ count: number }>('/api/settings/bot/known-contacts', { silent: true }))
  return known.data?.count ?? null
}

const AUTOSAVE_MS = 700

/** Self-contained card for the setup wizard: loads the bot settings and saves the rules as they change. */
export function ReplyRulesCard() {
  const { data, error, loading, reload } = useLoad(() => api.get<BotSettings>('/api/settings/bot', { silent: true }))
  const knownCount = useKnownCount()
  const [form, setForm] = useState<BotSettings | null>(null)
  const [state, setState] = useState<'idle' | 'saving' | 'saved'>('idle')
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useEffect(() => {
    if (data && !form) setForm(structuredClone(data))
  }, [data, form])
  useEffect(() => () => clearTimeout(timer.current), [])

  if (loading && !form) return <Spinner />
  if (error && !form) return <ErrorState message={error.message} onRetry={reload} />
  if (!form) return null

  function change(next: BotSettings) {
    setForm(next)
    clearTimeout(timer.current)
    timer.current = setTimeout(async () => {
      setState('saving')
      try {
        // the latest stored settings + only the rule fields: the prompt and the rest stay as they are
        const current = await api.get<BotSettings>('/api/settings/bot', { silent: true })
        const { new_contacts_only, exclude_phones, hours, work_hours_mode, after_hours_mode, after_hours_message, timezone, handover_pause_minutes } = next
        await api.put<BotSettings>('/api/settings/bot', {
          ...current,
          new_contacts_only,
          exclude_phones,
          hours,
          work_hours_mode,
          after_hours_mode,
          after_hours_message,
          timezone,
          handover_pause_minutes,
        })
        setState('saved')
      } catch {
        setState('idle') /* toast */
      }
    }, AUTOSAVE_MS)
  }

  return (
    <div className="flex flex-col gap-3">
      <ReplyRulesFields value={form} onChange={change} knownCount={knownCount} compact />
      <p className="text-[12px] text-ink-3" aria-live="polite">
        {state === 'saving' ? 'Сохраняем…' : state === 'saved' ? 'Сохранено. Изменить можно в любой момент в «Настройках бота».' : 'Изменения сохраняются сами.'}
      </p>
    </div>
  )
}
