import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { Coins, CreditCard, FileText, PlusCircle } from 'lucide-react'
import {
  api, coins, dialogsText, formatDay, goToCheckout, INVOICE_KIND, loadPublicConfig, money,
  type InvoiceListItem, type PublicConfig, type TopupOut, type UsageOut,
} from '../api'
import { Alert, Button, Card } from '../components/ui'
import { PaymentQr } from '../components/PaymentQr'
import { PayOnlineButton } from './PayOnline'

/** Cabinet: coin balance of the period, spend by day and model, "Пополнить" (online or through a manager), invoices. */
export function CoinsPanel() {
  const navigate = useNavigate()
  const [usage, setUsage] = useState<UsageOut | null>(null)
  const [invoices, setInvoices] = useState<InvoiceListItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [choosing, setChoosing] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [config, setConfig] = useState<PublicConfig | null>(null)
  const [qr, setQr] = useState<{ url: string; number: string | null } | null>(null)

  useEffect(() => {
    api.get<UsageOut>('/api/me/usage').then(setUsage).catch((err) => setError(err instanceof Error ? err.message : 'Ошибка'))
    api.get<InvoiceListItem[]>('/api/me/invoices').then(setInvoices).catch(() => undefined)
    loadPublicConfig()
      .then(setConfig)
      .catch(() => undefined)
  }, [])

  async function topup(method: 'online' | 'invoice') {
    setBusy(method)
    setError(null)
    setNotice(null)
    try {
      const r = await api.post<TopupOut>('/api/me/topup', { method })
      if (r.kind === 'online' && r.redirect_url && config?.online_mode === 'qr') {
        setQr({ url: r.redirect_url, number: r.invoice_number })
        setChoosing(false)
        setBusy(null)
        return
      }
      if (r.kind === 'online' && r.redirect_url) {
        goToCheckout(r.redirect_url)
        return
      }
      if (r.kind === 'invoice' && r.invoice_number) {
        navigate(`/invoice/${encodeURIComponent(r.invoice_number)}`)
        return
      }
      setChoosing(false)
      setNotice('Запрос отправлен. Менеджер выставит счёт на пакет коинов — он появится здесь и придёт на почту.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось отправить запрос')
    }
    setBusy(null)
  }

  const b = usage?.balance
  const total = b ? b.monthly_coins + b.topup_coins : 0
  const percent = b && total > 0 ? Math.max(0, Math.min(100, (b.coins_left / total) * 100)) : 0
  const maxDay = usage ? Math.max(1, ...usage.by_day.map((d) => d.coins)) : 1

  return (
    <Card className="p-6">
      <div className="flex flex-wrap items-center gap-3">
        <Coins className="size-6 text-accent" aria-hidden />
        <h2 className="text-[17px] font-extrabold text-ink">Коины</h2>
        <Button className="ml-auto" variant="primary" icon={<PlusCircle className="size-4" aria-hidden />} onClick={() => setChoosing((v) => !v)}>
          Пополнить
        </Button>
      </div>
      {choosing && (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-nx border border-line bg-sunken p-3" role="group" aria-label="Способ пополнения">
          <span className="w-full text-[13px] text-ink-2">Пакет 10 000 коинов, не сгорает в конце месяца.</span>
          {config?.online_payments && (
            <Button size="sm" variant="primary" icon={<CreditCard className="size-4" aria-hidden />} loading={busy === 'online'} onClick={() => void topup('online')}>
              Оплатить онлайн
            </Button>
          )}
          <Button size="sm" icon={<FileText className="size-4" aria-hidden />} loading={busy === 'invoice'} onClick={() => void topup('invoice')}>
            Счёт через менеджера
          </Button>
        </div>
      )}
      {notice && (
        <div className="mt-3">
          <Alert tone="ok">{notice}</Alert>
        </div>
      )}
      {qr && <PaymentQr url={qr.url} number={qr.number} />}
      {error && (
        <div className="mt-3">
          <Alert>{error}</Alert>
        </div>
      )}
      {b && (
        <>
          <div className="mt-4 flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="text-[32px] font-extrabold tabular-nums text-ink" data-testid="coins-left">
              {coins(b.coins_left)}
            </span>
            <span className="text-[14px] text-ink-2">коинов осталось</span>
            {b.dialogs_left !== undefined && (
              <span className="text-[14px] font-bold text-ink" data-testid="dialogs-left">
                {b.dialogs_left > 0 ? `${dialogsText(b.dialogs_left)}` : 'меньше одного диалога'}
              </span>
            )}
            {b.status !== 'active' && <span className="text-[13px] font-semibold text-danger">ключ отключён</span>}
          </div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-sunken" role="progressbar" aria-valuenow={Math.round(percent)} aria-valuemin={0} aria-valuemax={100}>
            <div className={percent < 10 ? 'h-full bg-danger' : percent < 25 ? 'h-full bg-warn' : 'h-full bg-ok'} style={{ width: `${percent}%` }} />
          </div>
          <dl className="mt-4 grid gap-x-6 gap-y-2 text-[13.5px] sm:grid-cols-3">
            <div>
              <dt className="text-[12px] font-semibold text-ink-3">Месячный пакет</dt>
              <dd className="text-ink">{coins(b.monthly_coins)} (до {formatDay(b.period_end)})</dd>
            </div>
            <div>
              <dt className="text-[12px] font-semibold text-ink-3">Докуплено, не сгорает</dt>
              <dd className="text-ink">{coins(b.topup_coins)}</dd>
            </div>
            <div>
              <dt className="text-[12px] font-semibold text-ink-3">Потрачено в этом месяце</dt>
              <dd className="text-ink">{coins(b.spent)}</dd>
            </div>
          </dl>
          <p className="mt-2 text-[12.5px] text-ink-3">
            Один диалог с клиентом в среднем стоит около {config?.coins_per_dialog ?? 37} коинов, сложные — дороже. 1 коин = $0.001
            расхода модели. Неиспользованный месячный пакет сгорает в конце месяца.
          </p>
        </>
      )}
      {usage && usage.by_day.length > 0 && (
        <div className="mt-5">
          <h3 className="text-[13.5px] font-bold text-ink">Расход по дням</h3>
          <ul className="mt-2 flex flex-col gap-1 text-[12.5px]" aria-label="Расход по дням">
            {usage.by_day.map((d) => (
              <li key={d.day} className="grid grid-cols-[84px_1fr_64px] items-center gap-2">
                <span className="text-ink-3 tabular-nums">{d.day.split('-').reverse().join('.')}</span>
                <span className="h-2 rounded-full bg-accent/70" style={{ width: `${Math.max(2, (d.coins / maxDay) * 100)}%` }} />
                <span className="text-right tabular-nums text-ink">{coins(d.coins)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {usage && usage.by_model.length > 0 && (
        <div className="mt-5 overflow-x-auto">
          <h3 className="text-[13.5px] font-bold text-ink">По моделям</h3>
          <table className="mt-2 w-full min-w-[420px] text-left text-[13px]">
            <thead>
              <tr className="text-[12px] text-ink-3">
                <th className="py-1 font-semibold">Модель</th>
                <th className="py-1 text-right font-semibold">Запросов</th>
                <th className="py-1 text-right font-semibold">Коинов</th>
              </tr>
            </thead>
            <tbody>
              {usage.by_model.map((m) => (
                <tr key={`${m.provider}/${m.model}`} className="border-t border-line">
                  <td className="py-1.5 font-mono text-[12.5px] text-ink">{m.model}</td>
                  <td className="py-1.5 text-right tabular-nums text-ink-2">{m.requests}</td>
                  <td className="py-1.5 text-right tabular-nums text-ink">{coins(m.coins)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {invoices.length > 0 && (
        <div className="mt-5">
          <h3 className="text-[13.5px] font-bold text-ink">Счета</h3>
          <ul className="mt-2 flex flex-col divide-y divide-line text-[13px]">
            {invoices.map((inv) => (
              <li key={inv.number} className="flex flex-wrap items-center gap-x-3 py-1.5">
                <Link to={inv.kind === 'subscription' ? '/invoice' : `/invoice/${encodeURIComponent(inv.number)}`} className="font-semibold text-accent hover:text-accent-hover">
                  № {inv.number}
                </Link>
                <span className="text-ink-2">{INVOICE_KIND[inv.kind]}</span>
                <span className="tabular-nums text-ink">{money(inv.amount_kzt)}</span>
                <span className={inv.paid ? 'text-ok' : 'text-warn'}>{inv.paid ? 'оплачен' : 'ждёт оплаты'}</span>
                {!inv.paid && inv.kind !== 'subscription' && (
                  <span className="ml-auto">
                    <PayOnlineButton number={inv.number} size="sm" />
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  )
}
