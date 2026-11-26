import { useState, type FormEvent, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { api, ApiError } from '../../api/client'
import { useAuth } from '../../hooks/useAuth'
import { useBrand } from '../../brand'
import { Wordmark } from '../../components/Layout'
import { Button, Field, Input } from '../../components/ui'

export function AuthFrame({ children, wide }: { children: ReactNode; wide?: boolean }) {
  return (
    <div className="flex min-h-dvh flex-col items-center px-4 py-10 sm:py-16">
      <Wordmark className="mb-8 text-[20px]" />
      <div className={wide ? 'w-full max-w-[640px]' : 'w-full max-w-[380px]'}>{children}</div>
    </div>
  )
}

/** ?email= from the cabinet's "Открыть панель бота" button: only pre-fills the field (no auto-login), then leaves the
 * address bar. */
function emailFromLink(): string {
  try {
    const url = new URL(window.location.href)
    if (!url.searchParams.has('email')) return ''
    const email = (url.searchParams.get('email') ?? '').trim()
    url.searchParams.delete('email')
    window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash)
    return /^\S+@\S+\.\S+$/.test(email) && email.length <= 254 ? email : ''
  } catch {
    return ''
  }
}

export function Login() {
  const { refresh } = useAuth()
  const navigate = useNavigate()
  const [fromCabinet] = useState(emailFromLink)
  const [email, setEmail] = useState(fromCabinet)
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const brand = useBrand()

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.post('/api/auth/login', { email: email.trim(), password }, { silent: true, noAuthRedirect: true })
      const state = await refresh()
      // an owner from the portal continues with the setup wizard (it starts after the owner step)
      navigate(state.wizardPending ? '/setup' : '/', { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setError('Неверный e-mail или пароль.')
      else setError(err instanceof Error ? err.message : 'Не удалось войти.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthFrame>
      <div className="rounded-[12px] border border-line bg-surface p-6 sm:p-7">
        <h1 className="text-[19px] font-semibold text-ink">Вход в {brand.name}</h1>
        <p className="mt-1 text-sm text-ink-2">Админка WhatsApp-ассистента.</p>
        {fromCabinet && (
          <p className="mt-3 rounded-[7px] bg-accent-soft px-3 py-2 text-[13px] text-ink" data-testid="cabinet-login-hint">
            Войдите e-mail и паролем от личного кабинета.
          </p>
        )}
        <form onSubmit={submit} className="mt-6 flex flex-col gap-4" noValidate>
          <Field label="E-mail">
            <Input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} autoFocus={!fromCabinet} />
          </Field>
          <Field label="Пароль">
            <Input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus={Boolean(fromCabinet)}
            />
          </Field>
          {error && (
            <p role="alert" className="rounded-[7px] bg-danger-soft px-3 py-2 text-[13px] text-danger">
              {error}
            </p>
          )}
          <Button type="submit" variant="primary" loading={busy} disabled={!email || !password} className="mt-1 w-full">
            Войти
          </Button>
        </form>
      </div>
      <p className="mt-4 text-center text-[13px] text-ink-3">Забыли пароль? Обратитесь к владельцу учётной записи.</p>
    </AuthFrame>
  )
}
