import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { Check, CheckCircle2, CircleAlert } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import type { AnthropicEffort, LlmProvider, LlmProvidersResult, LlmSettings, LlmTestResult, User } from '../../api/types'
import { useBrand } from '../../brand'
import { useAuth } from '../../hooks/useAuth'
import { useLoad } from '../../hooks/useLoad'
import { AuthFrame } from '../Login/Login'
import { WhatsAppConnect } from '../../components/WhatsAppConnect'
import { BotSetupStep } from './BotSetupStep'
import { TestBotStep } from './TestBotStep'
import { StepHeader } from './StepHeader'
import { Button, CheckLine, type CheckView, ErrorState, Field, Input, Select, Spinner, cx } from '../../components/ui'
import { ANTHROPIC_DEFAULT_MODEL, ANTHROPIC_EFFORT_HINT, ANTHROPIC_EFFORTS } from '../System/anthropic'
import {
  MODEL_FIELD,
  modelHint,
  ModelSelect,
  NoVendorKeys,
  PROVIDER_LABEL,
  RECOMMENDED,
  pickModel,
  type LlmModelsResult,
} from '../System/models'

const PROVIDER_DESC: Record<LlmProvider, string> = {
  anthropic: 'Claude Sonnet и Haiku',
  openai: 'Модели GPT',
  gemini: 'Модели Gemini от Google',
}

const STEPS = ['Владелец', 'Языковая модель', 'WhatsApp', 'Настройка бота', 'Проверьте бота', 'Готово'] as const
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
  const { phase, refresh } = useAuth()
  const navigate = useNavigate()
  const [step, setStep] = useState(0)
  const [botReady, setBotReady] = useState(false)
  const [token] = useState(captureSetupToken)

  // Wizard is only reachable while the server reports needs_setup (or mid-wizard).
  useEffect(() => {
    if (step === 0 && phase === 'ready') navigate('/', { replace: true })
    if (step === 0 && phase === 'anonymous') navigate('/login', { replace: true })
  }, [phase, step, navigate])

  const next = () => setStep((s) => Math.min(s + 1, STEPS.length - 1))

  return (
    <AuthFrame wide>
      <ol className="mb-6 flex flex-wrap items-center gap-x-2 gap-y-2 text-[13px]" aria-label="Шаги первоначальной настройки">
        {STEPS.map((label, i) => (
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
              {i < step ? <Check className="size-3.5" /> : i + 1}
            </span>
            <span className={cx(i === step ? 'font-medium text-ink' : 'text-ink-2', 'max-sm:sr-only')}>{label}</span>
            {i < STEPS.length - 1 && <span aria-hidden className="mx-1 h-px w-5 bg-line-strong" />}
          </li>
        ))}
      </ol>
      <div className="rounded-[12px] border border-line bg-surface p-6 sm:p-8">
        {step === 0 && <OwnerStep token={token} onDone={next} />}
        {step === 1 && <LlmStep onDone={next} />}
        {step === 2 && <WhatsAppStep onDone={next} />}
        {step === 3 && (
          <BotSetupStep
            onDone={(saved) => {
              setBotReady(saved)
              next()
            }}
          />
        )}
        {step === 4 && <TestBotStep ready={botReady} onDone={next} />}
        {step === 5 && (
          <div className="flex flex-col items-start gap-4">
            {botReady ? <CheckCircle2 className="size-8 text-ok" aria-hidden /> : <CircleAlert className="size-8 text-warn" aria-hidden />}
            <div>
              <h1 className="text-[20px] font-semibold text-ink">{botReady ? 'Бот готов к работе' : 'Почти готово'}</h1>
              <p className="mt-1 max-w-[56ch] text-sm text-ink-2">
                {botReady
                  ? 'Проверьте ответы в «Тестовом чате». Промпт и поведение бота можно поменять в разделе «Настройки бота».'
                  : 'Бот не отвечает клиентам, пока не настроен промпт. Выберите шаблон в разделе «Настройки бота».'}
              </p>
            </div>
            <Button
              variant="primary"
              onClick={async () => {
                await refresh()
                navigate('/', { replace: true })
              }}
            >
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

export function LlmStep({ onDone }: { onDone: () => void }) {
  const brand = useBrand()
  const current = useLoad(() => api.get<LlmSettings>('/api/settings/llm', { silent: true }))
  const available = useLoad(() => api.get<LlmProvidersResult>('/api/settings/llm/providers', { silent: true }))
  const [chosenProvider, setProvider] = useState<LlmProvider | null>(null)
  const [model, setModel] = useState<Record<LlmProvider, string>>({
    openai: RECOMMENDED.openai[0],
    gemini: RECOMMENDED.gemini[0],
    anthropic: ANTHROPIC_DEFAULT_MODEL,
  })
  const [models, setModels] = useState<Record<LlmProvider, string[] | null>>({ openai: null, gemini: null, anthropic: null })
  const [effort, setEffort] = useState<AnthropicEffort>('low')
  const [busy, setBusy] = useState(false)
  const [check, setCheck] = useState<CheckView | null>(null)

  const providers = available.data?.providers ?? []
  // the user's pick, else the stored provider if ILER has a key for it, else the first available one
  const provider: LlmProvider | null =
    chosenProvider ?? (current.data && providers.includes(current.data.provider) ? current.data.provider : (providers[0] ?? null))

  function payloadFor(p: LlmProvider, chosen: string): LlmSettings | null {
    if (!current.data) return null
    return {
      ...current.data,
      provider: p,
      [MODEL_FIELD[p]]: chosen,
      ...(p === 'anthropic' ? { anthropic_effort: effort } : {}),
    }
  }

  // Populate the model select as soon as an OpenAI / Gemini provider is selected.
  const settingsLoaded = Boolean(current.data)
  useEffect(() => {
    if (!settingsLoaded || !provider || provider === 'anthropic' || models[provider]) return
    let cancelled = false
    api
      .post<LlmModelsResult>('/api/settings/llm/models', payloadFor(provider, model[provider]), { silent: true })
      .then((r) => {
        if (cancelled || !r.ok) return // the check button shows the provider error
        setModels((m) => ({ ...m, [provider]: r.models }))
        setModel((m) => ({ ...m, [provider]: pickModel(provider, m[provider], r.models) }))
      })
      .catch(() => {
        /* the check button reports it */
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, settingsLoaded])

  async function verify(e: FormEvent) {
    e.preventDefault()
    if (!provider) return
    const base = payloadFor(provider, model[provider])
    if (!base) return
    setBusy(true)
    setCheck(null)
    try {
      // 1) list the provider's models (checks the connection without spending tokens)
      const listed = await api.post<LlmModelsResult>('/api/settings/llm/models', base)
      if (!listed.ok) {
        setCheck({ ok: false, message: listed.error })
        return
      }
      const chosen = pickModel(provider, model[provider], listed.models)
      setModels((m) => ({ ...m, [provider]: listed.models }))
      setModel((m) => ({ ...m, [provider]: chosen }))
      // 2) a one-word request with the chosen model; store the settings only when it works
      const payload = payloadFor(provider, chosen)!
      const r = await api.post<LlmTestResult>('/api/settings/llm/test', payload)
      if (r.ok) await api.put<LlmSettings>('/api/settings/llm', payload)
      setCheck({ ok: r.ok, message: r.ok ? undefined : r.error })
    } catch {
      /* toast */
    } finally {
      setBusy(false)
    }
  }

  async function proceed() {
    // the model may have been changed after the check: store the final choice
    const payload = provider ? payloadFor(provider, model[provider]) : null
    if (!payload) return
    setBusy(true)
    try {
      await api.put<LlmSettings>('/api/settings/llm', payload)
      onDone()
    } catch {
      /* toast */
    } finally {
      setBusy(false)
    }
  }

  const loadError = current.error ?? available.error
  const later = (
    <button type="button" onClick={onDone} className="self-start text-[13px] text-ink-2 underline hover:text-ink">
      Настроить позже
    </button>
  )

  return (
    <>
      <StepHeader
        title="Подключите языковую модель"
        text={`Бот формулирует ответы через подключение ${brand.name} к OpenAI, Google Gemini или Anthropic Claude. Ключи доступа предоставляет ${brand.company} — вам остаётся выбрать модель.`}
      />
      {current.loading || available.loading ? (
        <Spinner />
      ) : loadError ? (
        <ErrorState
          message={loadError.message}
          onRetry={() => {
            void current.reload()
            void available.reload()
          }}
        />
      ) : !provider ? (
        <div className="flex flex-col gap-5">
          <NoVendorKeys />
          {later}
        </div>
      ) : (
        <form onSubmit={verify} className="flex flex-col gap-5">
          <fieldset>
            <legend className="mb-2 text-[13px] font-medium text-ink">Провайдер</legend>
            <div className="grid gap-3 sm:grid-cols-3">
              {providers.map((value) => (
                <label
                  key={value}
                  className={cx(
                    'flex cursor-pointer items-start gap-3 rounded-[9px] border p-3.5 transition-colors',
                    provider === value ? 'border-accent bg-accent-soft' : 'border-line hover:border-line-strong',
                  )}
                >
                  <input
                    type="radio"
                    name="provider"
                    value={value}
                    checked={provider === value}
                    onChange={() => {
                      setProvider(value)
                      setCheck(null)
                    }}
                    className="mt-1 accent-[var(--color-accent)]"
                  />
                  <span>
                    <span className="block text-sm font-medium text-ink">{PROVIDER_LABEL[value]}</span>
                    <span className="block text-[13px] text-ink-2">{PROVIDER_DESC[value]}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Модель" hint={modelHint(brand)[provider]}>
              <ModelSelect
                key={provider}
                provider={provider}
                value={model[provider]}
                models={models[provider]}
                onChange={(m) => setModel((s) => ({ ...s, [provider]: m }))}
              />
            </Field>
            {provider === 'anthropic' && (
              <Field label="Глубина размышлений" hint={ANTHROPIC_EFFORT_HINT}>
                <Select value={effort} onChange={(e) => setEffort(e.target.value as AnthropicEffort)}>
                  {ANTHROPIC_EFFORTS.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" variant={check?.ok ? 'secondary' : 'primary'} loading={busy && !check?.ok}>
              Проверить подключение
            </Button>
            {check?.ok && (
              <Button variant="primary" onClick={() => void proceed()} loading={busy}>
                Продолжить
              </Button>
            )}
            <CheckLine result={check} />
          </div>
          {later}
        </form>
      )}
    </>
  )
}

function WhatsAppStep({ onDone }: { onDone: () => void }) {
  const [connected, setConnected] = useState(false)
  return (
    <>
      <StepHeader
        title="Подключите WhatsApp"
        text="Используйте телефон с номером, на который пишут клиенты. WhatsApp на телефоне продолжит работать как обычно."
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
