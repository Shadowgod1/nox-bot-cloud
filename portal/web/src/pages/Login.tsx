import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { api, ApiError } from '../api'
import { AuthCard } from '../components/Shell'
import { Alert, Button, Field, Input } from '../components/ui'
import { rememberEmail } from './pendingEmail'

export function Login() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    const normalized = email.trim().toLowerCase()
    try {
      await api.post('/api/auth/login', { email: normalized, password })
      navigate('/', { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.code === 'email_not_verified') {
        rememberEmail(normalized)
        navigate('/verify', { state: { email: normalized } })
        return
      }
      setError(err instanceof Error ? err.message : 'Не удалось войти.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthCard title="Вход в личный кабинет" lead="Статус заявки и доступ к вашему боту.">
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <Field label="E-mail">
          <Input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
        </Field>
        <Field label="Пароль">
          <Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        {error && <Alert>{error}</Alert>}
        <Button type="submit" variant="primary" loading={busy} disabled={!email || !password} className="w-full">
          Войти
        </Button>
        <div className="text-[13.5px]">
          <Link to="/reset" className="text-ink-2 hover:text-accent">
            Забыли пароль?
          </Link>
        </div>
        <div className="border-t border-line pt-4 text-center text-[14px] text-ink-2">
          Ещё нет аккаунта?{' '}
          <Link to="/register" className="font-semibold text-accent hover:text-accent-hover">
            Зарегистрироваться и подключить бота
          </Link>
        </div>
      </form>
    </AuthCard>
  )
}
