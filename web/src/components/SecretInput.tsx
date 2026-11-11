import { useId, useState } from 'react'
import { KeyRound } from 'lucide-react'
import { Button, Input, cx } from './ui'
import type { SecretValue } from '../api/types'

/**
 * Secret field with explicit intent.
 * The API returns secrets masked ("••••abcd") or null. The value handed to `onChange`
 * is exactly what must be sent back:
 *   - untouched  -> the original masked string (server keeps the stored secret);
 *   - replaced   -> the new plain value;
 *   - removed    -> null (only after the user pressed "Удалить").
 */
export interface SecretState {
  /** Value as loaded from the API (masked or null). */
  original: SecretValue
  mode: 'keep' | 'replace' | 'clear'
  draft: string
}

export function secretState(original: SecretValue): SecretState {
  return { original, mode: original ? 'keep' : 'replace', draft: '' }
}

/** What to send to the API for this field. */
export function secretPayload(s: SecretState): SecretValue {
  if (s.mode === 'clear') return null
  if (s.mode === 'replace') return s.draft.trim() ? s.draft.trim() : s.original
  return s.original
}

export function secretDirty(s: SecretState): boolean {
  return secretPayload(s) !== s.original
}

interface Props {
  label: string
  value: SecretState
  onChange: (s: SecretState) => void
  placeholder?: string
  hint?: string
}

export function SecretInput({ label, value, onChange, placeholder, hint }: Props) {
  const id = useId()
  const [reveal, setReveal] = useState(false)
  const stored = value.original
  const hintId = hint ? `${id}-hint` : undefined

  return (
    <div className="flex flex-col gap-1.5">
      {value.mode === 'replace' ? (
        <>
          <label htmlFor={id} className="text-[13px] font-medium text-ink">
            {label}
          </label>
          <div className="flex gap-2">
            <Input
              id={id}
              type={reveal ? 'text' : 'password'}
              autoComplete="off"
              spellCheck={false}
              value={value.draft}
              placeholder={placeholder}
              aria-describedby={hintId}
              onChange={(e) => onChange({ ...value, draft: e.target.value })}
              className="font-mono text-[13px]"
            />
            <Button variant="ghost" size="md" onClick={() => setReveal((r) => !r)} aria-pressed={reveal}>
              {reveal ? 'Скрыть' : 'Показать'}
            </Button>
            {stored && (
              <Button variant="ghost" onClick={() => onChange({ ...value, mode: 'keep', draft: '' })}>
                Отмена
              </Button>
            )}
          </div>
        </>
      ) : (
        <>
          <span className="text-[13px] font-medium text-ink">{label}</span>
          <div
            className={cx(
              'flex min-h-9 flex-wrap items-center justify-between gap-2 rounded-[7px] border px-3 py-1',
              value.mode === 'clear' ? 'border-danger/40 bg-danger-soft' : 'border-line bg-sunken',
            )}
          >
            <span className="flex items-center gap-2 text-sm">
              <KeyRound className="size-3.5 text-ink-3" aria-hidden />
              {value.mode === 'clear' ? (
                <span className="text-danger">Ключ будет удалён после сохранения</span>
              ) : (
                <>
                  <span className="text-ink-2">Сохранён</span>
                  <span className="font-mono text-[13px] text-ink">{stored}</span>
                </>
              )}
            </span>
            <span className="flex gap-1">
              {value.mode === 'clear' ? (
                <Button size="sm" variant="ghost" onClick={() => onChange({ ...value, mode: 'keep' })}>
                  Не удалять
                </Button>
              ) : (
                <>
                  <Button size="sm" variant="ghost" aria-label={`Заменить ${lower(label)}`} onClick={() => onChange({ ...value, mode: 'replace', draft: '' })}>
                    Заменить
                  </Button>
                  <Button size="sm" variant="danger-ghost" aria-label={`Удалить ${lower(label)}`} onClick={() => onChange({ ...value, mode: 'clear', draft: '' })}>
                    Удалить
                  </Button>
                </>
              )}
            </span>
          </div>
        </>
      )}
      {hint && (
        <p id={hintId} className="text-[13px] text-ink-2">
          {hint}
        </p>
      )}
    </div>
  )
}

function lower(label: string) {
  return label.charAt(0).toLowerCase() + label.slice(1)
}
