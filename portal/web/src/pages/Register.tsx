import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { api, ApiError } from '../api'
import { AuthCard } from '../components/Shell'
import { Alert, Button, Field, Input } from '../components/ui'
import { rememberEmail } from './pendingEmail'

const SLUG_RE = /^[a-z][a-z0-9-]{1,30}[a-z0-9]$/

export function slugProblem(slug: string): string | null {
  if (!slug) return null
  if (!SLUG_RE.test(slug)) return '3–32 символа: латинские буквы, цифры и дефис; начинается с буквы, не заканчивается дефисом'
  if (slug.includes('--')) return 'Два дефиса подряд нельзя'
  return null
}

interface SlugCheck {
  slug: string
  available: boolean
  message: string | null
}

export function Register() {
  const navigate = useNavigate()
  const [domain, setDomain] = useState('noxsw.kz')
  const [form, setForm] = useState({ company: '', contact_name: '', email: '', phone: '', slug: '', password: '', password2: '' })
  const [consent, setConsent] = useState(false)
  const [slugMsg, setSlugMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api
      .get<{ instance_domain: string }>('/api/public-config')
      .then((c) => setDomain(c.instance_domain))
      .catch(() => undefined)
  }, [])

  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: key === 'slug' ? e.target.value.toLowerCase().trim() : e.target.value }))

  async function checkSlug() {
    const local = slugProblem(form.slug)
    if (local || !form.slug) {
      setSlugMsg(local)
      return
    }
    try {
      const r = await api.get<SlugCheck>(`/api/slug-check?slug=${encodeURIComponent(form.slug)}`)
      setSlugMsg(r.available ? null : r.message)
    } catch {
      setSlugMsg(null)
    }
  }

  const passwordShort = form.password.length > 0 && form.password.length < 10
  const mismatch = form.password2.length > 0 && form.password !== form.password2
  const complete =
    form.company.trim() &&
    form.contact_name.trim() &&
    form.email.trim() &&
    form.phone.trim() &&
    form.slug &&
    form.password.length >= 10 &&
    form.password === form.password2 &&
    consent

  async function submit(e: FormEvent) {
    e.preventDefault()
    const local = slugProblem(form.slug)
    if (local) {
      setSlugMsg(local)
      return
    }
    setBusy(true)
    setError(null)
    try {
      const email = form.email.trim().toLowerCase()
      await api.post('/api/auth/register', {
        company: form.company,
        contact_name: form.contact_name,
        email,
        phone: form.phone,
        slug: form.slug,
        password: form.password,
        consent,
      })
      rememberEmail(email)
      navigate('/verify', { state: { email } })
    } catch (err) {
      if (err instanceof ApiError && err.code === 'slug_taken') setSlugMsg(err.message)
      else setError(err instanceof Error ? err.message : 'Не удалось отправить заявку.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthCard
      title="Подключить Nox Bot"
      lead="WhatsApp-ассистент для вашей компании. Заполните заявку — мы проверим её и создадим бота."
    >
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <Field label="Название компании">
          <Input value={form.company} onChange={set('company')} autoComplete="organization" required maxLength={200} />
        </Field>
        <Field label="Контактное лицо">
          <Input value={form.contact_name} onChange={set('contact_name')} autoComplete="name" required maxLength={200} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="E-mail">
            <Input type="email" value={form.email} onChange={set('email')} autoComplete="email" required />
          </Field>
          <Field label="Телефон">
            <Input type="tel" value={form.phone} onChange={set('phone')} autoComplete="tel" placeholder="+7 7__ ___ __ __" required />
          </Field>
        </div>
        <Field label="Адрес бота" hint={!slugMsg ? `Панель бота будет по адресу https://${form.slug || 'имя'}.${domain}` : undefined} error={slugMsg}>
          <div className="flex items-stretch">
            <Input
              value={form.slug}
              onChange={set('slug')}
              onBlur={checkSlug}
              className="rounded-r-none"
              placeholder="company"
              autoCapitalize="none"
              spellCheck={false}
              maxLength={32}
              aria-label="Адрес бота"
            />
            <span className="flex items-center rounded-r-nx border border-l-0 border-line-strong bg-sunken px-3 text-[14px] text-ink-3">
              .{domain}
            </span>
          </div>
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Пароль" error={passwordShort ? 'Не короче 10 символов' : null}>
            <Input type="password" value={form.password} onChange={set('password')} autoComplete="new-password" required />
          </Field>
          <Field label="Повторите пароль" error={mismatch ? 'Пароли не совпадают' : null}>
            <Input type="password" value={form.password2} onChange={set('password2')} autoComplete="new-password" required />
          </Field>
        </div>
        <label className="flex items-start gap-2.5 text-[13.5px] text-ink-2">
          <input
            type="checkbox"
            checked={consent}
            onChange={(e) => setConsent(e.target.checked)}
            className="mt-0.5 size-4 accent-[var(--nx-accent)]"
          />
          <span>Согласен на сбор и обработку персональных данных для подключения и обслуживания бота.</span>
        </label>
        {error && <Alert>{error}</Alert>}
        <Button type="submit" variant="primary" loading={busy} disabled={!complete} className="mt-1 w-full">
          Отправить заявку
        </Button>
        <p className="text-center text-[13.5px] text-ink-2">
          Уже есть аккаунт?{' '}
          <Link to="/login" className="font-semibold text-accent hover:text-accent-hover">
            Войти
          </Link>
        </p>
      </form>
    </AuthCard>
  )
}
