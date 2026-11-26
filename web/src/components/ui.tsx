import {
  cloneElement,
  isValidElement,
  useId,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactElement,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react'
import { AlertTriangle, Inbox, Loader2, RotateCw } from 'lucide-react'
import { ru } from '../i18n/ru'

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(' ')
}

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'danger-ghost'

const variants: Record<ButtonVariant, string> = {
  primary: 'bg-accent-solid text-white hover:bg-accent-hover border border-transparent',
  secondary: 'bg-surface text-ink border border-line-strong hover:bg-sunken',
  ghost: 'bg-transparent text-ink-2 hover:bg-sunken hover:text-ink border border-transparent',
  danger: 'bg-danger-solid text-white hover:brightness-95 border border-transparent',
  'danger-ghost': 'bg-transparent text-danger hover:bg-danger-soft border border-transparent',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: 'sm' | 'md'
  loading?: boolean
  icon?: ReactNode
}

export function Button({
  variant = 'secondary',
  size = 'md',
  loading = false,
  icon,
  className,
  children,
  disabled,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cx(
        'inline-flex items-center justify-center gap-1.5 rounded-[7px] font-medium whitespace-nowrap',
        'transition-[background-color,color,filter] duration-150 ease-out',
        'disabled:cursor-not-allowed disabled:opacity-55',
        size === 'sm' ? 'h-8 px-2.5 text-[13px]' : 'h-9 px-3.5 text-sm',
        variants[variant],
        className,
      )}
      {...rest}
    >
      {loading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  )
}

interface FieldProps {
  label: ReactNode
  hint?: ReactNode
  error?: string | null
  children: ReactElement<{ id?: string; 'aria-describedby'?: string; 'aria-invalid'?: boolean }>
  className?: string
  /** Visually hide the label (still read by screen readers). */
  hideLabel?: boolean
}

/** Label + control + hint/error, wired with ids for assistive tech. */
export function Field({ label, hint, error, children, className, hideLabel }: FieldProps) {
  const id = useId()
  const hintId = hint ? `${id}-hint` : undefined
  const errId = error ? `${id}-err` : undefined
  const describedBy = [hintId, errId].filter(Boolean).join(' ') || undefined
  const control = isValidElement(children)
    ? cloneElement(children, {
        id: children.props.id ?? id,
        'aria-describedby': describedBy,
        'aria-invalid': error ? true : undefined,
      })
    : children
  return (
    <div className={cx('flex flex-col gap-1.5', className)}>
      <label htmlFor={children.props.id ?? id} className={cx('text-[13px] font-medium text-ink', hideLabel && 'sr-only')}>
        {label}
      </label>
      {control}
      {hint && !error && (
        <p id={hintId} className="text-[13px] text-ink-2">
          {hint}
        </p>
      )}
      {error && (
        <p id={errId} className="text-[13px] text-danger">
          {error}
        </p>
      )}
    </div>
  )
}

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx('field-control h-9', className)} {...rest} />
}

export function Textarea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cx('field-control min-h-20 resize-y leading-relaxed', className)} {...rest} />
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cx('field-control h-9 pr-8', className)} {...rest}>
      {children}
    </select>
  )
}

interface SwitchProps {
  checked: boolean
  onChange: (v: boolean) => void
  label: string
  description?: ReactNode
  disabled?: boolean
}

export function Switch({ checked, onChange, label, description, disabled }: SwitchProps) {
  const id = useId()
  return (
    <div className="flex items-start justify-between gap-6">
      <div className="min-w-0">
        <label htmlFor={id} className="text-sm font-medium text-ink">
          {label}
        </label>
        {description && <p className="mt-0.5 text-[13px] text-ink-2">{description}</p>}
      </div>
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cx(
          'relative mt-0.5 inline-flex h-[22px] w-[38px] shrink-0 items-center rounded-full transition-colors duration-150',
          checked ? 'bg-accent' : 'bg-line-strong',
          'disabled:opacity-50',
        )}
      >
        <span
          aria-hidden
          className={cx(
            'inline-block size-[18px] rounded-full bg-white shadow-sm transition-transform duration-150 ease-out',
            checked ? 'translate-x-[18px]' : 'translate-x-[2px]',
          )}
        />
      </button>
    </div>
  )
}

interface PanelProps {
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  id?: string
  bodyClassName?: string
}

export function Panel({ title, description, actions, children, className, id, bodyClassName }: PanelProps) {
  const headingId = useId()
  return (
    <section
      id={id}
      aria-labelledby={title ? headingId : undefined}
      className={cx('rounded-[var(--radius-panel)] border border-line bg-surface', className)}
    >
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0">
            {title && (
              <h2 id={headingId} className="text-[15px] font-semibold text-ink">
                {title}
              </h2>
            )}
            {description && <p className="mt-0.5 max-w-[68ch] text-[13px] text-ink-2">{description}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cx('px-5 py-4', bodyClassName)}>{children}</div>
    </section>
  )
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-[22px] font-semibold leading-tight text-ink">{title}</h1>
        {description && <p className="mt-1 max-w-[70ch] text-sm text-ink-2">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}

export function Spinner({ label = ru.common.loading, className }: { label?: string; className?: string }) {
  return (
    <div role="status" className={cx('flex items-center justify-center gap-2 py-10 text-sm text-ink-2', className)}>
      <Loader2 className="size-4 animate-spin" aria-hidden />
      <span>{label}</span>
    </div>
  )
}

export function EmptyState({ title, children, icon, action }: { title: string; children?: ReactNode; icon?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
      <div className="mb-3 grid size-10 place-items-center rounded-full bg-sunken text-ink-3" aria-hidden>
        {icon ?? <Inbox className="size-5" />}
      </div>
      <p className="text-sm font-medium text-ink">{title}</p>
      {children && <p className="mt-1 max-w-[46ch] text-[13px] text-ink-2">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message?: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-col items-center justify-center px-6 py-12 text-center">
      <div className="mb-3 grid size-10 place-items-center rounded-full bg-danger-soft text-danger" aria-hidden>
        <AlertTriangle className="size-5" />
      </div>
      <p className="text-sm font-medium text-ink">{ru.common.loadError}</p>
      {message && <p className="mt-1 max-w-[46ch] text-[13px] text-ink-2">{message}</p>}
      {onRetry && (
        <Button size="sm" className="mt-4" onClick={onRetry} icon={<RotateCw className="size-3.5" aria-hidden />}>
          {ru.common.retry}
        </Button>
      )}
    </div>
  )
}

type Tone = 'neutral' | 'accent' | 'ok' | 'warn' | 'danger'
const tones: Record<Tone, string> = {
  neutral: 'bg-sunken text-ink-2',
  accent: 'bg-accent-soft text-accent-ink',
  ok: 'bg-ok-soft text-ok',
  warn: 'bg-warn-soft text-warn',
  danger: 'bg-danger-soft text-danger',
}

export function Badge({ tone = 'neutral', children, dot }: { tone?: Tone; children: ReactNode; dot?: boolean }) {
  return (
    <span className={cx('inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[12px] font-medium whitespace-nowrap', tones[tone])}>
      {dot && <span aria-hidden className="size-1.5 rounded-full bg-current" />}
      {children}
    </span>
  )
}

export interface SegmentOption<T extends string> {
  value: T
  label: string
  count?: number
}

/** Segmented filter (radio group semantics). */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: SegmentOption<T>[]
  value: T
  onChange: (v: T) => void
  label: string
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex flex-wrap gap-1 rounded-[8px] bg-sunken p-1">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cx(
            'rounded-[6px] px-2.5 py-1 text-[13px] font-medium transition-colors duration-150',
            value === o.value ? 'bg-surface text-ink shadow-[0_1px_2px_oklch(25%_0.02_230/0.12)]' : 'text-ink-2 hover:text-ink',
          )}
        >
          {o.label}
          {o.count !== undefined && o.count > 0 && <span className="tabular ml-1.5 text-ink-3">{o.count}</span>}
        </button>
      ))}
    </div>
  )
}

/** What a "Проверить" button shows: pages map the API test result (`{ok, error?, count?}`) to this. */
export interface CheckView {
  ok: boolean
  message?: string
}

/** Inline result line for "Проверить" buttons. */
export function CheckLine({ result }: { result: CheckView | null }) {
  if (!result) return null
  return (
    <p role="status" className={cx('text-[13px]', result.ok ? 'text-ok' : 'text-danger')}>
      {result.ok ? 'Работает' : 'Не работает'}
      {result.message ? `: ${result.message}` : ''}
    </p>
  )
}
