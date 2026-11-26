import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { ArrowLeft } from 'lucide-react'
import { api, money, type StaffConfig } from '../../api'
import { Shell } from '../../components/Shell'
import { Alert, Button, Card, Field, Input } from '../../components/ui'
import { OneTimeKey } from './GatewayPanel'
import { useStaff } from './useStaff'

interface Created {
  account_id: number
  prefix: string
  key: string
}

/** Staff: account for a bot installed on the client's machine; the gateway key is shown once. */
export function LocalAccountForm({ config }: { config: StaffConfig }) {
  const [form, setForm] = useState({ company: '', contact_name: '', email: '', phone: '', slug: '', tariff: config.tariffs[0]?.code ?? '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [created, setCreated] = useState<Created | null>(null)
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value })
  const ready = form.company.trim() && form.contact_name.trim() && form.email.includes('@') && form.phone.trim() && form.slug.trim()

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setCreated(await api.post<Created>('/api/staff/local-accounts', { ...form, slug: form.slug.trim().toLowerCase() }))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось создать аккаунт')
    } finally {
      setBusy(false)
    }
  }

  if (created) {
    return (
      <div className="flex flex-col gap-3">
        <Alert tone="ok">Аккаунт создан. Клиенту ушло письмо: как задать пароль кабинета.</Alert>
        <OneTimeKey value={created.key} gatewayUrl={config.gateway_url} />
        <Link to={`/staff/applications/${created.account_id}`} className="font-semibold text-accent hover:text-accent-hover">
          Открыть карточку
        </Link>
      </div>
    )
  }
  return (
    <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
      <Field label="Компания">
        <Input value={form.company} onChange={set('company')} maxLength={200} />
      </Field>
      <Field label="Контактное лицо">
        <Input value={form.contact_name} onChange={set('contact_name')} maxLength={200} />
      </Field>
      <Field label="E-mail клиента">
        <Input type="email" value={form.email} onChange={set('email')} />
      </Field>
      <Field label="Телефон">
        <Input value={form.phone} onChange={set('phone')} />
      </Field>
      <Field label="Короткое имя (латиница)" hint="Для карточки и счетов, например salon-aida">
        <Input value={form.slug} onChange={set('slug')} spellCheck={false} />
      </Field>
      <Field label="Тариф">
        <select
          value={form.tariff}
          onChange={set('tariff')}
          className="h-10 w-full rounded-nx border border-line-strong bg-raised px-3 text-[14px] text-ink focus:border-accent focus:outline-none"
        >
          {config.tariffs.map((t) => (
            <option key={t.code} value={t.code}>
              {t.name} — {money(t.price_kzt)}, {t.coins.toLocaleString('ru-RU')} коинов
            </option>
          ))}
        </select>
      </Field>
      {error && (
        <div className="sm:col-span-2">
          <Alert>{error}</Alert>
        </div>
      )}
      <div className="sm:col-span-2">
        <Button type="submit" variant="primary" loading={busy} disabled={!ready}>
          Создать и выпустить ключ
        </Button>
      </div>
    </form>
  )
}

export function LocalAccountPage() {
  const { staff, logout } = useStaff()
  const [config, setConfig] = useState<StaffConfig | null>(null)
  useEffect(() => {
    if (staff) api.get<StaffConfig>('/api/staff/config').then(setConfig).catch(() => undefined)
  }, [staff])
  return (
    <Shell subtitle="Локальная установка" home="/staff" user={staff?.email} onLogout={logout} wide>
      <Link to="/staff" className="inline-flex items-center gap-1 text-[13.5px] font-semibold text-ink-2 hover:text-accent">
        <ArrowLeft className="size-4" aria-hidden /> Все заявки
      </Link>
      <Card className="mt-4 max-w-[760px] p-5">
        <h1 className="text-[20px] font-extrabold text-ink">Локальная установка</h1>
        <p className="mt-1 mb-4 text-[13.5px] text-ink-2">
          Бот стоит у клиента. Портал ведёт баланс коинов и счета, а техник вписывает ключ шлюза в .env бота. Заданий провизионера нет.
        </p>
        {config && <LocalAccountForm config={config} />}
      </Card>
    </Shell>
  )
}
