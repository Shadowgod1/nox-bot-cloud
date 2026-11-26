import { useId, useState, type ClipboardEvent, type KeyboardEvent } from 'react'
import { Plus, X } from 'lucide-react'
import { Button, cx } from './ui'

/** Digits of a phone; a local 8XXXXXXXXXX becomes 7XXXXXXXXXX (as core normalizes it). */
export function phoneDigits(value: string): string {
  let d = value.replace(/\D/g, '')
  if (d.length === 11 && d.startsWith('8')) d = `7${d.slice(1)}`
  if (d.length === 10 && d.startsWith('7')) d = `7${d}` // "701 123 45 67" typed without the country code
  return d
}

/** "+7 701 123 45 67" for KZ/RU numbers, "+<digits>" otherwise. */
export function formatPhone(digits: string): string {
  const d = digits.replace(/\D/g, '')
  if (d.length === 11 && d.startsWith('7')) return `+7 ${d.slice(1, 4)} ${d.slice(4, 7)} ${d.slice(7, 9)} ${d.slice(9)}`
  return d ? `+${d}` : ''
}

/** Live mask while typing: "+7 7__ ___ __ __" style grouping of what was typed so far. */
export function maskPhoneInput(raw: string): string {
  let d = raw.replace(/\D/g, '')
  if (!d) return ''
  if (d.startsWith('8')) d = `7${d.slice(1)}`
  if (!d.startsWith('7')) return `+${d.slice(0, 15)}`
  d = d.slice(0, 11)
  const parts = [d.slice(1, 4), d.slice(4, 7), d.slice(7, 9), d.slice(9, 11)].filter(Boolean)
  return `+7${parts.length ? ' ' : ''}${parts.join(' ')}`
}

/** Numbers found in pasted text: one per line / cell / comma; anything with fewer than 10 digits is skipped. */
export function parsePhoneList(text: string): { phones: string[]; skipped: number } {
  const phones: string[] = []
  let skipped = 0
  for (const chunk of text.split(/[\n\r\t,;]+/)) {
    if (!chunk.trim()) continue
    const d = phoneDigits(chunk)
    if (d.length < 10 || d.length > 15) {
      skipped++
      continue
    }
    if (!phones.includes(d)) phones.push(d)
  }
  return { phones, skipped }
}

interface Props {
  label: string
  hint?: string
  value: string[]
  onChange: (phones: string[]) => void
  emptyText?: string
  max?: number
}

/**
 * List of phone numbers: typed one by one with a +7 mask, or pasted as a whole column from Excel /
 * Google Sheets / a notes app (every line becomes a number).
 */
export function PhoneListInput({ label, hint, value, onChange, emptyText = 'Список пуст', max = 2000 }: Props) {
  const id = useId()
  const [draft, setDraft] = useState('')
  const [note, setNote] = useState<string | null>(null)
  const digits = phoneDigits(draft)
  const valid = digits.length >= 11 && digits.length <= 15

  function add(list: string[], skipped = 0) {
    const fresh = list.filter((p) => !value.includes(p))
    const next = [...value, ...fresh].slice(0, max)
    onChange(next)
    const parts: string[] = []
    if (list.length > 1 || skipped) parts.push(`Добавлено номеров: ${next.length - value.length}`)
    if (list.length - fresh.length > 0 && list.length > 1) parts.push(`уже были в списке: ${list.length - fresh.length}`)
    if (skipped) parts.push(`не похожи на номер: ${skipped}`)
    setNote(parts.length ? parts.join(', ') : null)
  }

  function commit() {
    if (!valid) return
    add([digits])
    setDraft('')
  }

  function onPaste(e: ClipboardEvent<HTMLInputElement>) {
    const text = e.clipboardData.getData('text')
    if (!/[\n\r\t,;]/.test(text.trim())) return // a single number: let the mask handle it
    e.preventDefault()
    const { phones, skipped } = parsePhoneList(text)
    add(phones, skipped)
    setDraft('')
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') {
      e.preventDefault()
      commit()
    }
  }

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-[13px] font-medium text-ink">
        {label}
      </label>
      <div className="flex gap-2">
        <input
          id={id}
          inputMode="tel"
          autoComplete="off"
          value={draft}
          onChange={(e) => setDraft(maskPhoneInput(e.target.value))}
          onPaste={onPaste}
          onKeyDown={onKeyDown}
          placeholder="+7 7__ ___ __ __"
          aria-describedby={hint ? `${id}-hint` : undefined}
          className="field-control tabular min-w-0 flex-1"
        />
        <Button onClick={commit} disabled={!valid} icon={<Plus className="size-4" aria-hidden />}>
          Добавить
        </Button>
      </div>
      {hint && (
        <p id={`${id}-hint`} className="text-[12px] text-ink-3">
          {hint}
        </p>
      )}
      {note && (
        <p role="status" className="text-[12px] text-ink-2">
          {note}
        </p>
      )}
      {value.length === 0 ? (
        <p className="text-[13px] text-ink-3">{emptyText}</p>
      ) : (
        <ul className="flex flex-wrap gap-1.5" aria-label={label}>
          {value.map((p) => (
            <li key={p} className={cx('tabular flex items-center gap-1 rounded-full border border-line bg-sunken py-0.5 pr-1 pl-2.5 text-[13px] text-ink')}>
              {formatPhone(p)}
              <button
                type="button"
                onClick={() => onChange(value.filter((x) => x !== p))}
                className="grid size-5 place-items-center rounded-full text-ink-3 hover:bg-line hover:text-ink"
                aria-label={`Убрать ${formatPhone(p)}`}
              >
                <X className="size-3" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
