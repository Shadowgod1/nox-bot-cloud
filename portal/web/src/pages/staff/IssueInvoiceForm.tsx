import { useState, type FormEvent } from 'react'
import { api, money, type InvoiceKind, type Tariff } from '../../api'
import { Alert, Button, Field, Input } from '../../components/ui'

interface Props {
  applicationId: number
  kind: InvoiceKind
  /** paid tariffs (subscription / upgrade: the tariff can be changed) */
  tariffs: Tariff[]
  defaultTariff?: string | null
  /** renewal / top-up: the price to prefill */
  defaultAmount?: number | null
  /** top-up: coins of the pack */
  defaultCoins?: number | null
  submitLabel: string
  onDone: (number: string) => void
}

const SELECT_CLS =
  'h-10 w-full rounded-nx border border-line-strong bg-raised px-3 text-[15px] text-ink focus:border-accent focus:outline-none'

/** «Выставить счёт»: amount (defaults to the price, editable), optional comment; the client gets the invoice letter. */
export function IssueInvoiceForm({ applicationId, kind, tariffs, defaultTariff, defaultAmount, defaultCoins, submitLabel, onDone }: Props) {
  const withTariff = kind === 'subscription' || kind === 'upgrade'
  const initialTariff = tariffs.find((t) => t.code === defaultTariff)?.code ?? tariffs[0]?.code ?? ''
  const [tariff, setTariff] = useState(initialTariff)
  const priceOf = (code: string) => tariffs.find((t) => t.code === code)?.price_kzt ?? null
  const startAmount = withTariff ? priceOf(initialTariff) : defaultAmount ?? null
  const [amount, setAmount] = useState(startAmount != null ? String(startAmount) : '')
  const [coins, setCoins] = useState(defaultCoins != null ? String(defaultCoins) : '')
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const amountValue = /^\d{1,10}$/.test(amount) && Number(amount) > 0 ? Number(amount) : null
  const coinsValue = /^\d{1,9}$/.test(coins) && Number(coins) > 0 ? Number(coins) : null
  const invalid = amountValue === null || (withTariff && !tariff) || (kind === 'topup' && coinsValue === null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (invalid) return
    setBusy(true)
    setError(null)
    try {
      const body: Record<string, unknown> = { kind, amount_kzt: amountValue }
      if (withTariff) body.tariff = tariff
      if (kind === 'topup') body.coins = coinsValue
      if (comment.trim()) body.comment = comment.trim()
      const r = await api.post<{ number: string }>(`/api/staff/applications/${applicationId}/issue-invoice`, body)
      onDone(r.number)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось выставить счёт')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3" aria-label={submitLabel} noValidate>
      {withTariff && (
        <Field label="Тариф">
          <select
            className={SELECT_CLS}
            value={tariff}
            onChange={(e) => {
              setTariff(e.target.value)
              const p = priceOf(e.target.value)
              if (p != null) setAmount(String(p))
            }}
          >
            {tariffs.map((t) => (
              <option key={t.code} value={t.code}>
                {t.name} — {money(t.price_kzt)}
              </option>
            ))}
          </select>
        </Field>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Сумма счёта, ₸" error={amount && amountValue === null ? 'Целое число больше нуля' : null}>
          <Input value={amount} onChange={(e) => setAmount(e.target.value.replace(/\s/g, ''))} inputMode="numeric" />
        </Field>
        {kind === 'topup' && (
          <Field label="Коинов в пакете" error={coins && coinsValue === null ? 'Целое число больше нуля' : null}>
            <Input value={coins} onChange={(e) => setCoins(e.target.value.replace(/\s/g, ''))} inputMode="numeric" />
          </Field>
        )}
      </div>
      <Field label="Комментарий (необязательно, печатается в счёте)">
        <Input value={comment} onChange={(e) => setComment(e.target.value)} maxLength={500} />
      </Field>
      {error && <Alert>{error}</Alert>}
      <div>
        <Button type="submit" variant="primary" loading={busy} disabled={invalid}>
          {submitLabel}
        </Button>
      </div>
    </form>
  )
}
