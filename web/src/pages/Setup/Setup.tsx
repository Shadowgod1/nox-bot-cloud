import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { Check, CheckCircle2, CircleAlert } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import type { User } from '../../api/types'
import { useAuth } from '../../hooks/useAuth'
import { AuthFrame } from '../Login/Login'
import { WhatsAppConnect } from '../../components/WhatsAppConnect'
import { BotSetupStep } from './BotSetupStep'
import { StepHeader } from './StepHeader'
import { Button, Field, Input, cx } from '../../components/ui'
// as short as possible: the bot starts on Claude Haiku (the server default) with a universal prompt; the model,
// the test chat, lead notifications and reply rules live in the admin panel
const STEPS = ['Владелец', 'Ваш бот', 'WhatsApp', 'Готово'] as const
const TOKEN_KEY = 'iler_setup_token'
const TOKEN_HELP = 'Откройте ссылку из установщика (она содержит одноразовый токен).'

/** One-time setup token from the installer link (/setup?token=...); kept for this tab only. */
function captureSetupToken(): string | null {
  try {
    const url = new URL(window.location.href)
    const fromUrl = url.searchParams.get('token')
    if (fromUrl) {
      sessionStorage.setItem(TOKEN_KEY, fromUrl)
      url.searchParams.delete('token') // keep the token out of history and screenshots
      window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash)
      return fromUrl
    }
    return sessionStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function Setup() {
  const { phase, refresh, wizardPending } = useAuth()
  const navigate = useNavigate()
  const [rawStep, setStep] = useState(0)
  const [botReady, setBotReady] = useState(false)
  const [token] = useState(captureSetupToken)
  // the owner came from the Nox portal (same e-mail and password as the cabinet): no "owner" step, from the
  // very first render (kept after finishing, while the page navigates away)
  const [presetSeen, setPresetSeen] = useState(false)
  const presetOwner = presetSeen || (phase === 'ready' && wizardPending)
  const first = presetOwner ? 1 : 0
  const step = Math.max(rawStep, first)

  // Wizard is only reachable while the server reports needs_setup or wizard_pending (or mid-wizard).
  useEffect(() => {
    if (phase === 'ready' && wizardPending) {
      setPresetSeen(true)
      return
    }
    if (presetSeen) return
    if (step === 0 && phase === 'ready') navigate('/', { replace: true })
    if (step === 0 && phase === 'anonymous') navigate('/login', { replace: true })
  }, [phase, step, wizardPending, presetSeen, navigate])

  const next = () => setStep((s) => Math.min(Math.max(s, first) + 1, STEPS.length - 1))

  async function finish() {
    if (presetOwner) {
      try {
        await api.post('/api/setup/finish', {}, { silent: true })
      } catch {
        /* the wizard opens again after the next login; nothing is lost */
      }
    }
    await refresh()
    navigate('/', { replace: true })
  }

  return (
    <AuthFrame wide>
      <ol className="mb-6 flex flex-wrap items-center gap-x-2 gap-y-2 text-[13px]" aria-label="Шаги первоначальной настройки">
        {STEPS.slice(first).map((label, n) => {
          const i = n + first
          return (
          <li key={label} className="flex items-center gap-2" aria-current={i === step ? 'step' : undefined}>
            <span
              className={cx(
                'tabular grid size-6 place-items-center rounded-full text-[12px] font-semibold',
                i < step && 'bg-accent-solid text-white',
                i === step && 'bg-ink text-surface',
                i > step && 'bg-sunken text-ink-3',
              )}
              aria-hidden
            >
              {i < step ? <Check className="size-3.5" /> : n + 1}
            </span>
            <span className={cx(i === step ? 'font-medium text-ink' : 'text-ink-2', 'max-sm:sr-only')}>{label}</span>
            {i < STEPS.length - 1 && <span aria-hidden className="mx-1 h-px w-5 bg-line-strong" />}
          </li>
          )
        })}
      </ol>
      <div className="rounded-[12px] border border-line bg-surface p-6 sm:p-8">
        {step === 0 && <OwnerStep token={token} onDone={next} />}
        {step === 1 && (
          <BotSetupStep
            onDone={(saved) => {
              setBotReady(saved)
              next()
            }}
          />
        )}
        {step === 2 && <WhatsAppStep onDone={next} />}
        {step === 3 && (
          <div className="flex flex-col items-start gap-4">
            {botReady ? <CheckCircle2 className="size-8 text-ok" aria-hidden /> : <CircleAlert className="size-8 text-warn" aria-hidden />}
            <div>
              <h1 className="text-[20px] font-semibold text-ink">{botReady ? 'Бот запущен' : 'Почти готово'}</h1>
              <p className="mt-1 max-w-[56ch] text-sm text-ink-2">
                {botReady
                  ? 'Задайте боту пару вопросов в «Тестовом чате». Адрес, цены и промпт дописываются в «Настройках бота», уведомления о заявках — в «Интеграциях».'
                  : 'Бот не отвечает клиентам, пока не настроен промпт. Выберите шаблон в разделе «Настройки бота».'}
              </p>
            </div>
            <Button variant="primary" onClick={() => void finish()}>
              Перейти в админку
            </Button>
          </div>
        )}
      </div>
    </AuthFrame>
  )
}

function OwnerStep({ token, onDone }: { token: string | null; onDone: () => void }) {
  const { refresh } = useAuth()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [touched, setTouched] = useState(false)
  const [tokenError, setTokenError] = useState(false)

  const errs = {
    email: /^\S+@\S+\.\S+$/.test(email.trim()) ? null : 'Укажите e-mail',
    password: password.length >= 10 ? null : 'Не короче 10 символов',
    confirm: confirm === password ? null : 'Пароли не совпадают',
  }
  const valid = !errs.email && !errs.password && !errs.confirm

  async function submit(e: FormEvent) {
    e.preventDefault()
    setTouched(true)
    if (!valid) return
    setBusy(true)
    setTokenError(false)
    try {
      const creds = { email: email.trim(), password }
      // Core sets the session + CSRF cookies on success (same as /api/auth/login).
      await api.post<User>('/api/setup/owner', { ...creds, name: name.trim() }, {
        headers: token ? { 'X-ILER-Setup-Token': token } : undefined,
      })
      try {
        sessionStorage.removeItem(TOKEN_KEY) // one-time: used up
      } catch {
        /* storage unavailable */
      }
      // Defensive only: if the cookie did not stick (e.g. blocked), log in explicitly.
      try {
        await api.get<User>('/api/auth/me', { silent: true, noAuthRedirect: true })
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) await api.post<User>('/api/auth/login', creds, { noAuthRedirect: true })
        else throw err
      }
      onDone()
    } catch (err) {
      // 409 already_set_up: another owner was created meanwhile — the wizard is over, go to login.
      if (err instanceof ApiError && err.code === 'already_set_up') void refresh()
      if (err instanceof ApiError && (err.code === 'setup_token_required' || err.code === 'setup_token_invalid')) setTokenError(true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <StepHeader
        title="Создайте учётную запись владельца"
        text="Владелец управляет настройками, пользователями, обновлениями и резервными копиями. Других учётных записей пока нет."
      />
      <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2" noValidate>
        <Field label="Имя" className="sm:col-span-2">
          <Input autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </Field>
        <Field label="E-mail" error={touched ? errs.email : null} className="sm:col-span-2">
          <Input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Пароль" hint="Не короче 10 символов" error={touched ? errs.password : null}>
          <Input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Field label="Повторите пароль" error={touched ? errs.confirm : null}>
          <Input type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </Field>
        {tokenError && (
          <p role="alert" className="rounded-[8px] border border-danger/30 bg-danger-soft px-3.5 py-2.5 text-sm text-danger sm:col-span-2">
            {TOKEN_HELP}
          </p>
        )}
        <div className="sm:col-span-2">
          <Button type="submit" variant="primary" loading={busy}>
            Создать и продолжить
          </Button>
        </div>
      </form>
    </>
  )
}

function WhatsAppStep({ onDone }: { onDone: () => void }) {
  const [connected, setConnected] = useState(false)
  return (
    <>
      <StepHeader
        title="Подключите WhatsApp"
        text="Используйте телефон с номером, на который пишут клиенты. Можно подключить и позже — из раздела «WhatsApp»."
      />
      <WhatsAppConnect onConnected={() => setConnected(true)} />
      <div className="mt-6 flex items-center gap-3 border-t border-line pt-5">
        <Button variant={connected ? 'primary' : 'secondary'} onClick={onDone}>
          {connected ? 'Продолжить' : 'Подключу позже'}
        </Button>
      </div>
    </>
  )
}
