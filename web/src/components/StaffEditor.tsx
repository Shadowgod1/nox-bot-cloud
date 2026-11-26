import { useState } from 'react'
import { Plus, Trash2, UserRound } from 'lucide-react'
import { WEEKDAYS, type StaffDay, type StaffMember, type Weekday } from '../api/types'
import { Button, Field, Input, cx } from './ui'

export const STAFF_MAX = 50
export const DAY_SHORT: Record<Weekday, string> = { mon: 'Пн', tue: 'Вт', wed: 'Ср', thu: 'Чт', fri: 'Пт', sat: 'Сб', sun: 'Вс' }

export function newMember(): StaffMember {
  const day = (works: boolean): StaffDay => ({ works, open: '10:00', close: '19:00' })
  return {
    name: '',
    role: '',
    services: [],
    no_online_booking: false,
    note: '',
    mon: day(true),
    tue: day(true),
    wed: day(true),
    thu: day(true),
    fri: day(true),
    sat: day(false),
    sun: day(false),
  }
}

function sameHours(m: StaffMember): boolean {
  const days = WEEKDAYS.filter((d) => m[d].works)
  return days.every((d) => m[d].open === m[days[0]].open && m[d].close === m[days[0]].close)
}

/** Masters / doctors: name, services they do, a Mon–Sun grid with hours, "do not book online". */
export function StaffEditor({
  value,
  onChange,
  serviceNames,
  title = 'мастера',
}: {
  value: StaffMember[]
  onChange: (v: StaffMember[]) => void
  serviceNames: string[]
  title?: string
}) {
  const setMember = (i: number, m: StaffMember) => onChange(value.map((x, k) => (k === i ? m : x)))
  return (
    <div className="flex flex-col gap-3">
      {value.length === 0 && (
        <p className="rounded-[9px] border border-dashed border-line-strong px-4 py-5 text-center text-sm text-ink-2">
          Добавьте {title} — бот сможет ответить, кто в какой день работает. Время записи всё равно подтверждает администратор.
        </p>
      )}
      {value.map((m, i) => (
        <MemberCard
          key={i}
          index={i}
          member={m}
          serviceNames={serviceNames}
          onChange={(next) => setMember(i, next)}
          onRemove={() => onChange(value.filter((_, k) => k !== i))}
        />
      ))}
      <Button
        size="sm"
        variant={value.length ? 'ghost' : 'secondary'}
        className="self-start"
        disabled={value.length >= STAFF_MAX}
        onClick={() => onChange([...value, newMember()])}
        icon={<Plus className="size-3.5" aria-hidden />}
      >
        Добавить специалиста
      </Button>
    </div>
  )
}

function MemberCard({
  index,
  member: m,
  serviceNames,
  onChange,
  onRemove,
}: {
  index: number
  member: StaffMember
  serviceNames: string[]
  onChange: (m: StaffMember) => void
  onRemove: () => void
}) {
  const [perDay, setPerDay] = useState(() => !sameHours(m))
  const label = m.name.trim() || `Специалист ${index + 1}`
  const first = WEEKDAYS.find((d) => m[d].works) ?? 'mon'
  const setDay = (d: Weekday, patch: Partial<StaffDay>) => onChange({ ...m, [d]: { ...m[d], ...patch } })
  const setAllHours = (patch: Partial<StaffDay>) => {
    const next = { ...m }
    for (const d of WEEKDAYS) next[d] = { ...m[d], ...patch }
    onChange(next)
  }
  // services that are no longer in the price list stay selectable so the owner sees and can untick them
  const options = [...serviceNames, ...m.services.filter((s) => !serviceNames.includes(s))]

  return (
    <fieldset className="rounded-[10px] border border-line p-4">
      <legend className="sr-only">{label}</legend>
      <div className="flex items-start gap-3">
        <span className="mt-6 hidden size-9 shrink-0 place-items-center rounded-full bg-sunken text-ink-3 sm:grid" aria-hidden>
          <UserRound className="size-4" />
        </span>
        <div className="grid min-w-0 flex-1 gap-3 sm:grid-cols-2">
          <Field label="Имя">
            <Input value={m.name} maxLength={60} onChange={(e) => onChange({ ...m, name: e.target.value })} placeholder="Айгерим" />
          </Field>
          <Field label="Специализация" hint="Необязательно">
            <Input value={m.role} maxLength={120} onChange={(e) => onChange({ ...m, role: e.target.value })} placeholder="мастер маникюра" />
          </Field>
        </div>
        <Button size="sm" variant="danger-ghost" className="mt-6" aria-label={`Удалить: ${label}`} onClick={onRemove} icon={<Trash2 className="size-3.5" aria-hidden />} />
      </div>

      <div className="mt-4 grid gap-4">
        <div>
          <p className="mb-1.5 text-[13px] font-medium text-ink">Дни работы</p>
          <div role="group" aria-label={`${label}: дни работы`} className="flex flex-wrap gap-1.5">
            {WEEKDAYS.map((d) => (
              <button
                key={d}
                type="button"
                aria-pressed={m[d].works}
                onClick={() => setDay(d, { works: !m[d].works })}
                className={cx(
                  'h-9 w-11 rounded-[7px] border text-[13px] font-medium transition-colors',
                  m[d].works ? 'border-accent bg-accent-solid text-white' : 'border-line text-ink-2 hover:border-line-strong hover:text-ink',
                )}
              >
                {DAY_SHORT[d]}
              </button>
            ))}
          </div>
        </div>

        {WEEKDAYS.some((d) => m[d].works) && (
          <div className="flex flex-col gap-2">
            {!perDay ? (
              <div className="flex flex-wrap items-center gap-2 text-sm text-ink">
                <span className="text-[13px] text-ink-2">Часы</span>
                <Input type="time" aria-label={`${label}: начало работы`} value={m[first].open} onChange={(e) => e.target.value && setAllHours({ open: e.target.value })} className="tabular w-[110px]" />
                <span className="text-ink-3" aria-hidden>—</span>
                <Input type="time" aria-label={`${label}: конец работы`} value={m[first].close} onChange={(e) => e.target.value && setAllHours({ close: e.target.value })} className="tabular w-[110px]" />
              </div>
            ) : (
              <ul className="grid gap-1.5">
                {WEEKDAYS.filter((d) => m[d].works).map((d) => (
                  <li key={d} className="flex flex-wrap items-center gap-2">
                    <span className="w-7 text-[13px] text-ink-2">{DAY_SHORT[d]}</span>
                    <Input type="time" aria-label={`${label}, ${DAY_SHORT[d]}: начало`} value={m[d].open} onChange={(e) => e.target.value && setDay(d, { open: e.target.value })} className="tabular w-[110px]" />
                    <span className="text-ink-3" aria-hidden>—</span>
                    <Input type="time" aria-label={`${label}, ${DAY_SHORT[d]}: конец`} value={m[d].close} onChange={(e) => e.target.value && setDay(d, { close: e.target.value })} className="tabular w-[110px]" />
                  </li>
                ))}
              </ul>
            )}
            <label className="flex items-center gap-2 text-[13px] text-ink-2">
              <input
                type="checkbox"
                checked={perDay}
                onChange={(e) => {
                  setPerDay(e.target.checked)
                  if (!e.target.checked) setAllHours({ open: m[first].open, close: m[first].close })
                }}
                className="accent-[var(--color-accent)]"
              />
              Разные часы по дням
            </label>
          </div>
        )}

        {options.length > 0 && (
          <div>
            <p className="mb-1.5 text-[13px] font-medium text-ink">Услуги</p>
            <div role="group" aria-label={`${label}: услуги`} className="flex flex-wrap gap-1.5">
              {options.map((s) => {
                const on = m.services.includes(s)
                return (
                  <button
                    key={s}
                    type="button"
                    aria-pressed={on}
                    onClick={() => onChange({ ...m, services: on ? m.services.filter((x) => x !== s) : [...m.services, s] })}
                    className={cx(
                      'max-w-full truncate rounded-full border px-2.5 py-1 text-[13px] transition-colors',
                      on ? 'border-accent bg-accent-soft text-ink' : 'border-line text-ink-2 hover:border-line-strong hover:text-ink',
                    )}
                  >
                    {s}
                  </button>
                )
              })}
            </div>
          </div>
        )}

        <label className="flex items-start gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={m.no_online_booking}
            onChange={(e) => onChange({ ...m, no_online_booking: e.target.checked })}
            className="mt-1 accent-[var(--color-accent)]"
          />
          <span>
            Не записывать онлайн
            <span className="block text-[13px] text-ink-2">Бот расскажет о специалисте, но заявку к нему не оформит — передаст администратору.</span>
          </span>
        </label>
      </div>
    </fieldset>
  )
}

/** Members that can be saved: named, trimmed. */
export function cleanStaff(value: StaffMember[]): StaffMember[] {
  return value.filter((m) => m.name.trim()).map((m) => ({ ...m, name: m.name.trim(), role: m.role.trim() }))
}
