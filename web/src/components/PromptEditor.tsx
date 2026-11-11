import { useId, useLayoutEffect, useRef } from 'react'
import { cx } from './ui'

interface Props {
  label: string
  value: string
  onChange: (v: string) => void
  hint?: string
  placeholder?: string
  minRows?: number
  maxLength?: number
  /** Placeholders the text may contain, shown as insertable chips, e.g. ["{details}"]. */
  variables?: { token: string; title: string }[]
}

/** Auto-growing editor for prompt-like text with a length counter and insertable variables. */
export function PromptEditor({ label, value, onChange, hint, placeholder, minRows = 6, maxLength = 20000, variables }: Props) {
  const id = useId()
  const ref = useRef<HTMLTextAreaElement>(null)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight + 2, 640)}px`
  }, [value])

  function insert(token: string) {
    const el = ref.current
    if (!el) return onChange(value + token)
    const start = el.selectionStart ?? value.length
    const end = el.selectionEnd ?? value.length
    onChange(value.slice(0, start) + token + value.slice(end))
    requestAnimationFrame(() => {
      el.focus()
      el.setSelectionRange(start + token.length, start + token.length)
    })
  }

  const near = value.length > maxLength * 0.9
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <label htmlFor={id} className="text-[13px] font-medium text-ink">
          {label}
        </label>
        <span className={cx('tabular text-[12px]', near ? 'text-warn' : 'text-ink-3')} aria-live="polite">
          {value.length.toLocaleString('ru-RU')} / {maxLength.toLocaleString('ru-RU')}
        </span>
      </div>
      <textarea
        ref={ref}
        id={id}
        rows={minRows}
        value={value}
        maxLength={maxLength}
        placeholder={placeholder}
        aria-describedby={hint ? `${id}-hint` : undefined}
        onChange={(e) => onChange(e.target.value)}
        className="field-control resize-y leading-relaxed"
        spellCheck
      />
      {(hint || variables?.length) && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          {hint && (
            <p id={`${id}-hint`} className="text-[13px] text-ink-2">
              {hint}
            </p>
          )}
          {variables?.length ? (
            <div className="flex flex-wrap gap-1.5" aria-label="Вставить переменную">
              {variables.map((v) => (
                <button
                  key={v.token}
                  type="button"
                  title={v.title}
                  onClick={() => insert(v.token)}
                  className="rounded-[5px] border border-line bg-sunken px-1.5 py-0.5 font-mono text-[12px] text-ink-2 hover:border-line-strong hover:text-ink"
                >
                  {v.token}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}
