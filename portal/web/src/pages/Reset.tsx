import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { api } from '../api'
import { AuthCard } from '../components/Shell'
import { Alert, Button, Field, Input } from '../components/ui'

export function Reset() {
  const [step, setStep] = useState<'email' | 'code' | 'done'>('email')
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [password2, setPassword2] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function requestCode(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.post('/api/auth/reset/request', { email: email.trim().toLowerCase() })
      setStep('code')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось отправить код.')
    } finally {
      setBusy(false)
    }
  }

  async function confirm(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.post('/api/auth/reset/confirm', { email: email.trim().toLowerCase(), code, password })
      setStep('done')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось сменить пароль.')
    } finally {
      setBusy(false)
    }
  }

  if (step === 'done') {
    return (
      <AuthCard title="Пароль изменён">
        <Alert tone="ok">Новый пароль сохранён. Все прежние сеансы завершены.</Alert>
        <Link to="/login" className="mt-5 inline-block font-semibold text-accent hover:text-accent-hover">
          Войти с новым паролем
        </Link>
      </AuthCard>
    )
  }

  return (
    <AuthCard
      title="Сброс пароля"
      lead={step === 'email' ? 'Пришлём код на e-mail, указанный при регистрации.' : 'Если аккаунт с этим адресом есть, код уже в почте.'}
    >
      {step === 'email' ? (
        <form onSubmit={requestCode} className="flex flex-col gap-4" noValidate>
          <Field label="E-mail">
            <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" autoFocus />
          </Field>
          {error && <Alert>{error}</Alert>}
          <Button type="submit" variant="primary" loading={busy} disabled={!email} className="w-full">
            Получить код
          </Button>
        </form>
      ) : (
        <form onSubmit={confirm} className="flex flex-col gap-4" noValidate>
          <Field label="Код из письма">
            <Input
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              inputMode="numeric"
              autoComplete="one-time-code"
              className="text-center font-mono text-[20px] tracking-[0.4em]"
              autoFocus
            />
          </Field>
          <Field label="Новый пароль" error={password && password.length < 10 ? 'Не короче 10 символов' : null}>
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
          </Field>
          <Field label="Повторите пароль" error={password2 && password !== password2 ? 'Пароли не совпадают' : null}>
            <Input type="password" value={password2} onChange={(e) => setPassword2(e.target.value)} autoComplete="new-password" />
          </Field>
          {error && <Alert>{error}</Alert>}
          <Button
            type="submit"
            variant="primary"
            loading={busy}
            disabled={code.length !== 6 || password.length < 10 || password !== password2}
            className="w-full"
          >
            Сменить пароль
          </Button>
        </form>
      )}
      <p className="mt-4 text-center text-[13.5px]">
        <Link to="/login" className="text-ink-2 hover:text-accent">
          Вернуться ко входу
        </Link>
      </p>
    </AuthCard>
  )
}
