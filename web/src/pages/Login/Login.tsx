import { useState, type FormEvent, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { api, ApiError } from '../../api/client'
import { useAuth } from '../../hooks/useAuth'
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

export function Login() {
  const { refresh } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.post('/api/auth/login', { email: email.trim(), password }, { silent: true, noAuthRedirect: true })
      await refresh()
      navigate('/', { replace: true })
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
        <h1 className="text-[19px] font-semibold text-ink">Вход в ILER</h1>
        <p className="mt-1 text-sm text-ink-2">Админка WhatsApp-ассистента клуба.</p>
        <form onSubmit={submit} className="mt-6 flex flex-col gap-4" noValidate>
          <Field label="E-mail">
            <Input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
          </Field>
          <Field label="Пароль">
            <Input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
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
