import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { FlaskConical } from 'lucide-react'
import { api, ApiError, money } from '../api'
import { Shell } from '../components/Shell'
import { Alert, Button, Card } from '../components/ui'

interface MockPayment {
  payment_id: string
  purpose_ru: string
  amount_kzt: number
  status: string
  invoice_no: string | null
  return_url: string
}

interface Signed {
  webhook_url: string
  payload: string
  signature_header: string
  signature: string
}

/**
 * Fake checkout of the mock provider (NOX_PAYMENT_PROVIDER=mock, test stands only). «Оплачено» / «Отказ» asks the
 * api to sign a webhook payload and posts it to the public webhook — the same path a real provider uses.
 */
export function MockCheckout() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [payment, setPayment] = useState<MockPayment | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    api
      .get<MockPayment>(`/api/payments/mock/${encodeURIComponent(id)}`)
      .then(setPayment)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) navigate('/login', { replace: true })
        else setError(err instanceof Error ? err.message : 'Платёж не найден')
      })
  }, [id, navigate])

  async function send(result: 'succeeded' | 'failed') {
    if (!payment) return
    setBusy(result)
    setError(null)
    try {
      const s = await api.post<Signed>(`/api/payments/mock/${encodeURIComponent(id)}/sign`, { result })
      const res = await fetch(s.webhook_url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', [s.signature_header]: s.signature },
        body: s.payload,
      })
      if (!res.ok) throw new Error(`Webhook: HTTP ${res.status}`)
      navigate(payment.return_url)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось отправить результат')
      setBusy(null)
    }
  }

  return (
    <Shell subtitle="Тестовая оплата">
      <Card className="mx-auto max-w-[480px] p-6">
        <div className="flex items-center gap-2 text-warn">
          <FlaskConical className="size-5" aria-hidden />
          <span className="text-[13px] font-semibold">Тестовый платёжный шлюз: деньги не списываются</span>
        </div>
        {error && (
          <div className="mt-4">
            <Alert>{error}</Alert>
          </div>
        )}
        {payment && (
          <>
            <h1 className="mt-4 text-[20px] font-extrabold text-ink">{payment.purpose_ru}</h1>
            <p className="mt-1 text-[28px] font-extrabold tabular-nums text-ink" data-testid="mock-amount">
              {money(payment.amount_kzt)}
            </p>
            {payment.invoice_no && <p className="text-[13.5px] text-ink-3">Счёт № {payment.invoice_no}</p>}
            {payment.status !== 'pending' ? (
              <p className="mt-4 text-[14px] text-ink-2">Платёж уже обработан ({payment.status}).</p>
            ) : (
              <div className="mt-5 flex gap-2">
                <Button variant="primary" loading={busy === 'succeeded'} disabled={!!busy} onClick={() => void send('succeeded')}>
                  Оплачено
                </Button>
                <Button variant="danger" loading={busy === 'failed'} disabled={!!busy} onClick={() => void send('failed')}>
                  Отказ
                </Button>
              </div>
            )}
          </>
        )}
      </Card>
    </Shell>
  )
}
