import {
  cloneElement,
  isValidElement,
  useId,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactElement,
  type ReactNode,
} from 'react'
import { Loader2 } from 'lucide-react'
import { STATUS_LABELS, type Status } from '../api'

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(' ')
}

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'

const variants: Record<Variant, string> = {
  primary: 'bg-accent text-white hover:bg-accent-hover border border-transparent',
  secondary: 'bg-raised text-ink border border-line-strong hover:bg-sunken',
  ghost: 'bg-transparent text-ink-2 hover:bg-sunken hover:text-ink border border-transparent',
  danger: 'bg-danger text-white hover:brightness-95 border border-transparent',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
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
        'inline-flex items-center justify-center gap-1.5 rounded-nx font-semibold whitespace-nowrap',
        'transition-[background-color,color,filter] duration-150 ease-out',
        'disabled:cursor-not-allowed disabled:opacity-55',
        size === 'sm' ? 'h-8 px-2.5 text-[13px]' : 'h-10 px-4 text-sm',
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
}

/** Label + control + hint/error, wired with ids for assistive tech. */
export function Field({ label, hint, error, children, className }: FieldProps) {
  const id = useId()
  const hintId = `${id}-hint`
  const control = isValidElement(children)
    ? cloneElement(children, {
        id,
        'aria-describedby': hint || error ? hintId : undefined,
        'aria-invalid': error ? true : undefined,
      })
    : children
  return (
    <div className={cx('flex flex-col gap-1.5', className)}>
      <label htmlFor={id} className="text-[13px] font-semibold text-ink-2">
        {label}
      </label>
      {control}
      {(error || hint) && (
        <p id={hintId} className={cx('text-[12.5px]', error ? 'text-danger' : 'text-ink-3')}>
          {error || hint}
        </p>
      )}
    </div>
  )
}

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cx(
        'h-10 w-full rounded-nx border border-line-strong bg-raised px-3 text-[15px] text-ink',
        'placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent-soft',
        'aria-[invalid=true]:border-danger',
        className,
      )}
      {...rest}
    />
  )
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx('rounded-nx border border-line bg-surface', className)}>{children}</div>
}

export function Alert({ tone = 'danger', children }: { tone?: 'danger' | 'ok' | 'warn' | 'info'; children: ReactNode }) {
  const tones = {
    danger: 'bg-danger-soft text-danger',
    ok: 'bg-ok-soft text-ok',
    warn: 'bg-warn-soft text-warn',
    info: 'bg-accent-soft text-accent-ink',
  }
  return (
    <div role={tone === 'danger' ? 'alert' : 'status'} className={cx('rounded-nx px-3 py-2 text-[13.5px]', tones[tone])}>
      {children}
    </div>
  )
}

const STATUS_TONE: Record<Status, string> = {
  new: 'bg-sunken text-ink-2',
  verified: 'bg-accent-soft text-accent-ink',
  approved: 'bg-accent-soft text-accent-ink',
  provisioning: 'bg-warn-soft text-warn',
  active: 'bg-ok-soft text-ok',
  failed: 'bg-danger-soft text-danger',
  rejected: 'bg-sunken text-ink-3',
  suspended: 'bg-warn-soft text-warn',
  removed: 'bg-sunken text-ink-3',
}

export function StatusBadge({ status }: { status: Status }) {
  return (
    <span className={cx('inline-flex items-center rounded-nx px-2 py-0.5 text-[12.5px] font-semibold', STATUS_TONE[status])}>
      {STATUS_LABELS[status]}
    </span>
  )
}
