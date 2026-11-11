import type { ComponentType } from 'react'
import { BriefcaseBusiness, FileText, Gamepad2, Scissors, Stethoscope } from 'lucide-react'
import { api } from '../api/client'
import type { TemplateField, TemplateMeta, TemplateRenderResult } from '../api/types'
import { Field, Input, Textarea, cx } from './ui'

/** Shared pieces of "Настройка бота": template choice, the template's field form, rendering the prompt. */

const ICONS: Record<string, ComponentType<{ className?: string; 'aria-hidden'?: boolean }>> = {
  computer_club: Gamepad2,
  beauty_salon: Scissors,
  clinic: Stethoscope,
  generic: BriefcaseBusiness,
}

export const VALUE_MAX = 4000
export const OVERWRITE_CONFIRM = 'Промпт был изменён вручную. Собрать его заново из шаблона? Ручные правки будут потеряны.'

/** Only the keys this template declares (core rejects unknown ones). */
export function valuesFor(template: TemplateMeta, values: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const f of template.fields) out[f.key] = values[f.key] ?? ''
  return out
}

export function missingRequired(template: TemplateMeta, values: Record<string, string>): string[] {
  return template.fields.filter((f) => f.required && !(values[f.key] ?? '').trim()).map((f) => f.key)
}

export function renderTemplate(template: TemplateMeta, values: Record<string, string>) {
  return api.post<TemplateRenderResult>(`/api/templates/${encodeURIComponent(template.id)}/render`, {
    values: valuesFor(template, values),
  })
}

export function TemplatePicker({
  templates,
  value,
  onChange,
}: {
  templates: TemplateMeta[]
  value: string | null
  onChange: (id: string) => void
}) {
  return (
    <div role="radiogroup" aria-label="Тип бизнеса" className="grid gap-2.5 sm:grid-cols-2">
      {templates.map((t) => {
        const Icon = ICONS[t.id] ?? FileText
        const active = value === t.id
        return (
          <label
            key={t.id}
            className={cx(
              'group flex cursor-pointer items-start gap-3 rounded-[9px] border p-3.5 transition-colors duration-150',
              'has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-accent',
              active ? 'border-accent bg-accent-soft' : 'border-line hover:border-line-strong',
            )}
          >
            <input
              type="radio"
              name="bot-template"
              value={t.id}
              checked={active}
              onChange={() => onChange(t.id)}
              className="sr-only"
            />
            <span
              className={cx(
                'grid size-9 shrink-0 place-items-center rounded-[8px] transition-colors duration-150',
                active ? 'bg-accent text-white' : 'bg-sunken text-ink-2 group-hover:text-ink',
              )}
            >
              <Icon className="size-[18px]" aria-hidden />
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-medium text-ink">{t.name}</span>
              <span className="mt-0.5 block text-[13px] leading-snug text-ink-2">{t.description}</span>
            </span>
          </label>
        )
      })}
    </div>
  )
}

interface ControlProps {
  field: TemplateField
  value: string
  onChange: (v: string) => void
  /** Wired by <Field> (cloneElement) for the label and hint/error. */
  id?: string
  'aria-describedby'?: string
  'aria-invalid'?: boolean
}

function FieldControl({ field, value, onChange, ...a11y }: ControlProps) {
  const common = {
    ...a11y,
    value,
    placeholder: field.placeholder || undefined,
    maxLength: VALUE_MAX,
    required: field.required,
    onChange: (e: { target: { value: string } }) => onChange(e.target.value),
  }
  if (field.type === 'textarea') return <Textarea rows={Math.min(6, Math.max(3, field.placeholder.split('\n').length + 1))} {...common} />
  if (field.type === 'phone') return <Input type="tel" inputMode="tel" autoComplete="off" className="tabular" {...common} />
  return <Input {...common} />
}

/** The template's form, in the template's order. Short fields pair up; text areas take the full row. */
export function TemplateFields({
  template,
  values,
  onChange,
  showErrors,
}: {
  template: TemplateMeta
  values: Record<string, string>
  onChange: (key: string, value: string) => void
  showErrors?: boolean
}) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {template.fields.map((f) => {
        const empty = !(values[f.key] ?? '').trim()
        return (
          <Field
            key={f.key}
            label={f.label}
            hint={f.hint || (f.required ? undefined : 'Необязательно')}
            error={showErrors && f.required && empty ? 'Заполните это поле' : null}
            className={f.type === 'textarea' ? 'sm:col-span-2' : undefined}
          >
            <FieldControl field={f} value={values[f.key] ?? ''} onChange={(v) => onChange(f.key, v)} />
          </Field>
        )
      })}
    </div>
  )
}
