import { useEffect, useState } from 'react'
import { CheckCircle2, Info, X, XCircle } from 'lucide-react'
import { dismissToast, subscribeToasts, type Toast } from './toast'
import { cx } from './ui'

const icons = {
  error: <XCircle className="size-4 shrink-0 text-danger" aria-hidden />,
  success: <CheckCircle2 className="size-4 shrink-0 text-ok" aria-hidden />,
  info: <Info className="size-4 shrink-0 text-accent" aria-hidden />,
}

export function ToastHost() {
  const [items, setItems] = useState<Toast[]>([])
  useEffect(() => subscribeToasts(setItems), [])
  return (
    <div aria-live="polite" className="pointer-events-none fixed right-4 bottom-4 z-50 flex w-[min(380px,calc(100vw-2rem))] flex-col gap-2">
      {items.map((t) => (
        <div
          key={t.id}
          role={t.kind === 'error' ? 'alert' : 'status'}
          className={cx(
            'pointer-events-auto flex items-start gap-2.5 rounded-[9px] border border-line bg-surface px-3.5 py-3 text-sm text-ink',
            'shadow-[var(--shadow-pop)] [animation:toast-in_220ms_cubic-bezier(0.16,1,0.3,1)]',
          )}
        >
          <span className="mt-0.5">{icons[t.kind]}</span>
          <p className="min-w-0 flex-1 break-words">{t.message}</p>
          <button
            type="button"
            onClick={() => dismissToast(t.id)}
            className="-m-1 rounded p-1 text-ink-3 hover:bg-sunken hover:text-ink"
            aria-label="Закрыть уведомление"
          >
            <X className="size-3.5" aria-hidden />
          </button>
        </div>
      ))}
    </div>
  )
}
