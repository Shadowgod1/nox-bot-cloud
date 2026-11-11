import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { Check, CheckCircle2 } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import type { LlmProvider, LlmSettings, LlmTestResult, TemplateMeta, User } from '../../api/types'
import { useAuth } from '../../hooks/useAuth'
import { useLoad } from '../../hooks/useLoad'
import { AuthFrame } from '../Login/Login'
import { WhatsAppConnect } from '../../components/WhatsAppConnect'
import { Button, CheckLine, type CheckView, EmptyState, ErrorState, Field, Input, Spinner, cx } from '../../components/ui'

const STEPS = ['Владелец', 'Языковая модель', 'Шаблон', 'WhatsApp', 'Готово'] as const

export function Setup() {
  const { phase, refresh } = useAuth()
  const navigate = useNavigate()
  const [step, setStep] = useState(0)

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
                i < step && 'bg-accent text-white',
                i === step && 'bg-ink text-white',
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
        {step === 0 && <OwnerStep onDone={next} />}
        {step === 1 && <LlmStep onDone={next} />}
        {step === 2 && <TemplateStep onDone={next} />}
        {step === 3 && <WhatsAppStep onDone={next} />}
        {step === 4 && (
          <div className="flex flex-col items-start gap-4">
            <CheckCircle2 className="size-8 text-ok" aria-hidden />
            <div>
              <h1 className="text-[20px] font-semibold text-ink">Бот готов к работе</h1>
              <p className="mt-1 max-w-[56ch] text-sm text-ink-2">
                Дальше стоит заполнить цены, адрес и часы работы в разделе «Настройки бота» и проверить ответы в тестовом чате.
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

function StepHeader({ title, text }: { title: string; text: string }) {
  return (
    <div className="mb-6">
      <h1 className="text-[20px] font-semibold text-ink">{title}</h1>
      <p className="mt-1 max-w-[60ch] text-sm text-ink-2">{text}</p>
    </div>
  )
}

function OwnerStep({ onDone }: { onDone: () => void }) {
  const { refresh } = useAuth()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [touched, setTouched] = useState(false)

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
    try {
      const creds = { email: email.trim(), password }
      // Core sets the session + CSRF cookies on success (same as /api/auth/login).
      await api.post<User>('/api/setup/owner', { ...creds, name: name.trim() })
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
        <div className="sm:col-span-2">
          <Button type="submit" variant="primary" loading={busy}>
            Создать и продолжить
          </Button>
        </div>
      </form>
    </>
  )
}

function LlmStep({ onDone }: { onDone: () => void }) {
  const current = useLoad(() => api.get<LlmSettings>('/api/settings/llm', { silent: true }))
  const [provider, setProvider] = useState<LlmProvider>('openai')
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [check, setCheck] = useState<CheckView | null>(null)

  async function verify(e: FormEvent) {
    e.preventDefault()
    if (!current.data || !key.trim()) return
    setBusy(true)
    setCheck(null)
    try {
      const payload: LlmSettings = {
        ...current.data,
        provider,
        [provider === 'openai' ? 'openai_key' : 'gemini_key']: key.trim(),
      }
      // Test the unsaved key first (core saves nothing on /test), store it only when it works.
      const r = await api.post<LlmTestResult>('/api/settings/llm/test', payload)
      if (r.ok) await api.put<LlmSettings>('/api/settings/llm', payload)
      setCheck({ ok: r.ok, message: r.ok ? undefined : r.error })
    } catch {
      /* toast */
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <StepHeader
        title="Подключите языковую модель"
        text="Бот формулирует ответы с помощью OpenAI или Google Gemini. Ключ хранится только на этом сервере в зашифрованном виде."
      />
      {current.loading ? (
        <Spinner />
      ) : current.error ? (
        <ErrorState message={current.error.message} onRetry={current.reload} />
      ) : (
        <form onSubmit={verify} className="flex flex-col gap-5">
          <fieldset>
            <legend className="mb-2 text-[13px] font-medium text-ink">Провайдер</legend>
            <div className="grid gap-3 sm:grid-cols-2">
              {(
                [
                  ['openai', 'OpenAI', 'Ключ вида sk-… из platform.openai.com'],
                  ['gemini', 'Google Gemini', 'Ключ из Google AI Studio'],
                ] as const
              ).map(([value, title, desc]) => (
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
                    <span className="block text-sm font-medium text-ink">{title}</span>
                    <span className="block text-[13px] text-ink-2">{desc}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <Field label="Ключ API">
            <Input
              type="password"
              autoComplete="off"
              spellCheck={false}
              className="font-mono text-[13px]"
              value={key}
              onChange={(e) => {
                setKey(e.target.value)
                setCheck(null)
              }}
            />
          </Field>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" variant={check?.ok ? 'secondary' : 'primary'} loading={busy} disabled={!key.trim()}>
              Проверить ключ
            </Button>
            {check?.ok && (
              <Button variant="primary" onClick={onDone}>
                Продолжить
              </Button>
            )}
            <CheckLine result={check} />
          </div>
          <button type="button" onClick={onDone} className="self-start text-[13px] text-ink-2 underline hover:text-ink">
            Настроить позже
          </button>
        </form>
      )}
    </>
  )
}

function TemplateStep({ onDone }: { onDone: () => void }) {
  const list = useLoad(() => api.get<TemplateMeta[]>('/api/templates', { silent: true }))
  const [choice, setChoice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (list.data?.length && !choice) setChoice(list.data[0].id)
  }, [list.data, choice])

  async function apply() {
    if (!choice) return
    setBusy(true)
    try {
      await api.post(`/api/templates/${encodeURIComponent(choice)}/apply`)
      onDone()
    } catch {
      /* toast */
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <StepHeader title="Выберите шаблон бота" text="Шаблон заполнит инструкции и типовые ответы. Всё можно изменить позже в настройках бота." />
      {list.loading ? (
        <Spinner />
      ) : list.error ? (
        <ErrorState message={list.error.message} onRetry={list.reload} />
      ) : !list.data?.length ? (
        <EmptyState title="Шаблоны не найдены" action={<Button onClick={onDone}>Продолжить без шаблона</Button>}>
          Бот начнёт с пустых настроек.
        </EmptyState>
      ) : (
        <div className="flex flex-col gap-5">
          <div role="radiogroup" aria-label="Шаблон" className="grid gap-3 sm:grid-cols-2">
            {list.data.map((t) => (
              <label
                key={t.id}
                className={cx(
                  'flex cursor-pointer items-start gap-3 rounded-[9px] border p-4 transition-colors',
                  choice === t.id ? 'border-accent bg-accent-soft' : 'border-line hover:border-line-strong',
                )}
              >
                <input type="radio" name="template" checked={choice === t.id} onChange={() => setChoice(t.id)} className="mt-1 accent-[var(--color-accent)]" />
                <span>
                  <span className="block text-sm font-medium text-ink">{t.name}</span>
                  <span className="mt-0.5 block text-[13px] text-ink-2">{t.description}</span>
                </span>
              </label>
            ))}
          </div>
          <div>
            <Button variant="primary" loading={busy} onClick={apply} disabled={!choice}>
              Применить и продолжить
            </Button>
          </div>
        </div>
      )}
    </>
  )
}

function WhatsAppStep({ onDone }: { onDone: () => void }) {
  const [connected, setConnected] = useState(false)
  return (
    <>
      <StepHeader
        title="Подключите WhatsApp клуба"
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
