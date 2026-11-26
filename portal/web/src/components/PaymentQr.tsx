import { useMemo } from 'react'
import qrcode from 'qrcode-generator'
import { ExternalLink } from 'lucide-react'
import { money } from '../api'

/** QR of a payment link (Kaspi): scan with the phone camera or the Kaspi app; «Открыть в Kaspi» on the phone. */
export function PaymentQr({ url, amount, number }: { url: string; amount?: number | null; number?: string | null }) {
  const path = useMemo(() => {
    const qr = qrcode(0, 'M')
    qr.addData(url)
    qr.make()
    const n = qr.getModuleCount()
    let d = ''
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (qr.isDark(r, c)) d += `M${c + 4},${r + 4}h1v1h-1z`
    return { d, size: n + 8 }
  }, [url])

  return (
    <div className="mt-3 flex flex-wrap items-center gap-4 rounded-nx border border-line bg-raised p-4" data-testid="payment-qr">
      <svg
        viewBox={`0 0 ${path.size} ${path.size}`}
        width={176}
        height={176}
        role="img"
        aria-label="QR-код для оплаты в Kaspi"
        shapeRendering="crispEdges"
        className="shrink-0 rounded bg-white"
      >
        <rect width={path.size} height={path.size} fill="#fff" />
        <path d={path.d} fill="#000" />
      </svg>
      <div className="min-w-0 text-[13.5px] text-ink-2">
        <p className="font-semibold text-ink">Оплата через Kaspi</p>
        {amount != null && <p className="tabular-nums">К оплате: {money(amount)}</p>}
        {number && <p>Счёт № {number}</p>}
        <p className="mt-1 text-ink-3">Отсканируйте камерой телефона или в приложении Kaspi. С телефона — нажмите ссылку ниже.</p>
        <a href={url} className="mt-2 inline-flex items-center gap-1.5 font-semibold text-accent hover:text-accent-hover">
          Открыть в Kaspi <ExternalLink className="size-4" aria-hidden />
        </a>
      </div>
    </div>
  )
}
