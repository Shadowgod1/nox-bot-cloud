import { useEffect, useState } from 'react'
import { CreditCard } from 'lucide-react'
import { api, goToCheckout, loadPublicConfig, type CheckoutOut, type PublicConfig } from '../api'
import { PaymentQr } from '../components/PaymentQr'
import { Alert, Button } from '../components/ui'

export function usePublicConfig(): PublicConfig | null {
  const [config, setConfig] = useState<PublicConfig | null>(null)
  useEffect(() => {
    loadPublicConfig()
      .then(setConfig)
      .catch(() => undefined)
  }, [])
  return config
}

/** "Оплатить онлайн" for an issued invoice (shown only when an online provider is configured). */
export function PayOnlineButton({ number, size }: { number: string; size?: 'sm' | 'md' }) {
  const config = usePublicConfig()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [qr, setQr] = useState<string | null>(null)
  if (!config?.online_payments) return null
  if (qr) return <PaymentQr url={qr} number={number} />

  async function pay() {
    setBusy(true)
    setError(null)
    try {
      const r = await api.post<CheckoutOut>(`/api/me/invoices/${encodeURIComponent(number)}/pay-online`)
      if (config?.online_mode === 'qr' && (r.qr_payload || r.redirect_url)) {
        setQr(r.qr_payload || r.redirect_url)
        setBusy(false)
      } else if (r.redirect_url) goToCheckout(r.redirect_url)
      else setBusy(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось начать оплату')
      setBusy(false)
    }
  }

  return (
    <>
      <Button size={size} variant="secondary" icon={<CreditCard className="size-4" aria-hidden />} loading={busy} onClick={pay}>
        Оплатить онлайн
      </Button>
      {error && <Alert>{error}</Alert>}
    </>
  )
}
