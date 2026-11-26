import { useState } from 'react'
import { Link } from 'react-router'
import { Copy, KeyRound } from 'lucide-react'
import { api, coins, formatDate, formatDay, INVOICE_KIND, money, type ApplicationDetail, type KeyIssued } from '../../api'
import { Alert, Button, Field, Input } from '../../components/ui'

/** A full virtual key, shown exactly once (local installs). */
export function OneTimeKey({ value, gatewayUrl }: { value: string; gatewayUrl?: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="rounded-nx border border-warn/40 bg-warn-soft p-3">
      <p className="text-[13px] font-semibold text-warn">Ключ показывается один раз. Передайте его технику, в портале он больше не появится.</p>
      <div className="mt-2 flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded-nx bg-raised px-2 py-1.5 font-mono text-[12.5px] text-ink" data-testid="one-time-key">
          {value}
        </code>
        <Button size="sm" icon={<Copy className="size-4" aria-hidden />} onClick={() => void navigator.clipboard?.writeText(value).then(() => setCopied(true))}>
          {copied ? 'Скопировано' : 'Копировать'}
        </Button>
      </div>
      {gatewayUrl && (
        <p className="mt-2 text-[12.5px] text-ink-2">
          В .env бота: <code>ILER_LLM_GATEWAY_URL={gatewayUrl}</code> и <code>ILER_LLM_GATEWAY_KEY=&lt;ключ&gt;</code>
        </p>
      )}
    </div>
  )
}

/** Staff: gateway key (prefix, reissue, revoke) and coin balance of an account. */
export function GatewayPanel({ app, gatewayUrl, onChanged }: { app: ApplicationDetail; gatewayUrl?: string; onChanged: () => void }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [shown, setShown] = useState<string | null>(null)
  const active = (app.keys ?? []).filter((k) => k.status === 'active')
  const canReissue = app.local_install ? ['active', 'suspended'].includes(app.status) : app.status === 'active'

  async function reissue() {
    const warn = app.local_install
      ? 'Выпустить новый ключ? Старый перестанет работать сразу — новый нужно вписать в .env бота.'
      : 'Выпустить новый ключ? Провизионер впишет его в .env бота и перезапустит ядро; старый отключится после этого.'
    if (!window.confirm(warn)) return
    setBusy('reissue')
    setError(null)
    setNotice(null)
    try {
      const r = await api.post<KeyIssued>(`/api/staff/applications/${app.id}/key/reissue`)
      if (r.key) setShown(r.key)
      else setNotice(`Новый ключ ${r.prefix}… передаётся боту (задание провизионера)`)
      onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось выпустить ключ')
    } finally {
      setBusy(null)
    }
  }

  async function revoke() {
    if (!window.confirm('Отозвать все ключи шлюза? Бот сразу перестанет получать ответы модели.')) return
    setBusy('revoke')
    setError(null)
    try {
      await api.post(`/api/staff/applications/${app.id}/key/revoke`)
      setNotice('Ключи отозваны')
      onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось отозвать ключ')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="flex flex-col gap-3 text-[13.5px]">
      {app.llm ? (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
          <div>
            <dt className="text-[12px] font-semibold text-ink-3">Осталось коинов</dt>
            <dd className="text-[18px] font-extrabold tabular-nums text-ink">{coins(app.llm.coins_left)}</dd>
          </div>
          <div>
            <dt className="text-[12px] font-semibold text-ink-3">Потрачено</dt>
            <dd className="tabular-nums text-ink">{coins(app.llm.spent)}</dd>
          </div>
          <div>
            <dt className="text-[12px] font-semibold text-ink-3">Месячный / докуплено</dt>
            <dd className="tabular-nums text-ink">
              {coins(app.llm.monthly_coins)} / {coins(app.llm.topup_coins)}
            </dd>
          </div>
          <div>
            <dt className="text-[12px] font-semibold text-ink-3">Период до</dt>
            <dd className="text-ink">{formatDay(app.llm.period_end)}</dd>
          </div>
        </dl>
      ) : (
        <p className="text-ink-3">Шлюз не подключён (бот с собственным ключом или ещё не одобрен).</p>
      )}
      <div>
        <div className="text-[12px] font-semibold text-ink-3">Ключ</div>
        {active.length ? (
          active.map((k) => (
            <div key={k.id} className="font-mono text-[13px] text-ink">
              {k.prefix}… <span className="font-sans text-ink-3">с {formatDate(k.created_at)}</span>
            </div>
          ))
        ) : (
          <div className="text-ink-3">нет активного ключа</div>
        )}
      </div>
      {shown && <OneTimeKey value={shown} gatewayUrl={gatewayUrl} />}
      {notice && <Alert tone="ok">{notice}</Alert>}
      {error && <Alert>{error}</Alert>}
      <div className="flex flex-wrap gap-2">
        <Button icon={<KeyRound className="size-4" aria-hidden />} loading={busy === 'reissue'} disabled={!canReissue} onClick={reissue}>
          Перевыпустить ключ
        </Button>
        <Button variant="danger" loading={busy === 'revoke'} disabled={!active.length} onClick={revoke}>
          Отозвать
        </Button>
      </div>
    </div>
  )
}

/** Staff: renewal / top-up invoices with "Отметить оплату". */
export function InvoicesPanel({ app, onChanged }: { app: ApplicationDetail; onChanged: () => void }) {
  const [open, setOpen] = useState<number | null>(null)
  const [amount, setAmount] = useState('')
  const [reference, setReference] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const list = (app.invoices ?? []).filter((i) => i.kind !== 'subscription')
  if (!list.length) return <p className="text-[13.5px] text-ink-3">Счетов на продление и пополнение нет</p>

  async function submit(id: number) {
    setBusy(true)
    setError(null)
    try {
      await api.post(`/api/staff/invoices/${id}/mark-paid`, { amount_kzt: Number(amount), reference: reference.trim() })
      setOpen(null)
      onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось отметить оплату')
    } finally {
      setBusy(false)
    }
  }

  return (
    <ul className="flex flex-col divide-y divide-line text-[13.5px]">
      {list.map((inv) => (
        <li key={inv.number} className="py-2">
          <div className="flex flex-wrap items-center gap-x-3">
            <Link to={`/staff/applications/${app.id}/invoices/${encodeURIComponent(inv.number)}`} className="font-semibold text-accent hover:text-accent-hover">
              № {inv.number}
            </Link>
            <span className="text-ink-2">{INVOICE_KIND[inv.kind]}</span>
            <span className="tabular-nums">{money(inv.amount_kzt)}</span>
            {inv.coins !== null && <span className="text-ink-3">{coins(inv.coins)} коинов</span>}
            {inv.kind === 'renewal' && <span className="text-ink-3">с {formatDay(inv.period_start)}</span>}
            <span className={inv.paid ? 'text-ok' : 'text-warn'}>{inv.paid ? 'оплачен' : 'ждёт оплаты'}</span>
            {!inv.paid && inv.id !== null && open !== inv.id && (
              <Button
                size="sm"
                className="ml-auto"
                onClick={() => {
                  setOpen(inv.id)
                  setAmount(String(inv.amount_kzt))
                  setReference('')
                }}
              >
                Отметить оплату
              </Button>
            )}
          </div>
          {open === inv.id && inv.id !== null && (
            <div className="mt-2 grid gap-2 sm:grid-cols-[140px_1fr_auto] sm:items-end">
              <Field label="Получено, ₸">
                <Input inputMode="numeric" value={amount} onChange={(e) => setAmount(e.target.value.replace(/\D/g, ''))} />
              </Field>
              <Field label="Платёжное поручение">
                <Input value={reference} onChange={(e) => setReference(e.target.value)} maxLength={200} />
              </Field>
              <Button variant="primary" loading={busy} disabled={!Number(amount)} onClick={() => submit(inv.id as number)}>
                Записать оплату
              </Button>
            </div>
          )}
        </li>
      ))}
      {error && (
        <li className="pt-2">
          <Alert>{error}</Alert>
        </li>
      )}
    </ul>
  )
}
