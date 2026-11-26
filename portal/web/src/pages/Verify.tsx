import { useEffect, useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { api, ApiError } from '../api'
import { AuthCard } from '../components/Shell'
import { Alert, Button, Field, Input } from '../components/ui'
import { forgetEmail, recallEmail, rememberEmail } from './pendingEmail'

const COOLDOWN = 60

export function Verify() {
  const navigate = useNavigate()
  const location = useLocation()
  const fromState = (location.state as { email?: string } | null)?.email
  const [email, setEmail] = useState(fromState ?? recallEmail())
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [wait, setWait] = useState(fromState ? COOLDOWN : 0)

  useEffect(() => {
    if (wait <= 0) return
    const t = setTimeout(() => setWait((w) => w - 1), 1000)
    return () => clearTimeout(t)
  }, [wait])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.post('/api/auth/verify', { email: email.trim().toLowerCase(), code })
      forgetEmail()
      navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось подтвердить код.')
    } finally {
      setBusy(false)
    }
  }

  async function resend() {
    setError(null)
    setInfo(null)
    try {
      await api.post('/api/auth/resend', { email: email.trim().toLowerCase() })
      rememberEmail(email.trim().toLowerCase())
      setInfo('Если заявка с этим адресом ожидает подтверждения, мы отправили новый код.')
      setWait(COOLDOWN)
    } catch (err) {
      if (err instanceof ApiError && err.code === 'resend_cooldown') setWait(COOLDOWN)
      setError(err instanceof Error ? err.message : 'Не удалось отправить код.')
    }
  }

  return (
    <AuthCard title="Подтвердите e-mail" lead="Мы отправили 6-значный код на вашу почту. Код действует 15 минут.">
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <Field label="E-mail">
          <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />
        </Field>
        <Field label="Код из письма">
          <Input
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
            inputMode="numeric"
            autoComplete="one-time-code"
            placeholder="000000"
            className="text-center font-mono text-[20px] tracking-[0.4em]"
            autoFocus
          />
        </Field>
        {error && <Alert>{error}</Alert>}
        {info && <Alert tone="info">{info}</Alert>}
        <Button type="submit" variant="primary" loading={busy} disabled={code.length !== 6 || !email} className="w-full">
          Подтвердить
        </Button>
        <div className="flex items-center justify-between text-[13.5px]">
          <button
            type="button"
            onClick={resend}
            disabled={wait > 0 || !email}
            className="font-semibold text-accent hover:text-accent-hover disabled:cursor-not-allowed disabled:text-ink-3"
          >
            {wait > 0 ? `Отправить код повторно через ${wait} с` : 'Отправить код повторно'}
          </button>
          <Link to="/login" className="text-ink-2 hover:text-accent">
            Войти
          </Link>
        </div>
      </form>
    </AuthCard>
  )
}
