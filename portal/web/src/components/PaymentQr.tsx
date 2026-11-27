import { useMemo } from 'react'
import qrcode from 'qrcode-generator'
import { ExternalLink } from 'lucide-react'
import { money } from '../api'

/** QR of a payment link (Kaspi): scan with the phone camera or the Kaspi app; «Открыть в Kaspi» on the phone. */
export function PaymentQr({ url, amount, number }: { url: string; amount?: number | null; number?: string | null }) {
  const path = useQrPath(url)
  return <PaymentQrView url={url} amount={amount} number={number} path={path} />
}

function useQrPath(url: string) {
  return useMemo(() => {
    const qr = qrcode(0, 'M')
    qr.addData(url)
    qr.make()
    const n = qr.getModuleCount()
    let d = ''
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (qr.isDark(r, c)) d += `M${c + 4},${r + 4}h1v1h-1z`
    return { d, size: n + 8 }
  }, [url])
}

function QrSvg({ path, label, size = 176 }: { path: { d: string; size: number }; label: string; size?: number }) {
  return (
    <svg
      viewBox={`0 0 ${path.size} ${path.size}`}
      width={size}
      height={size}
      role="img"
      aria-label={label}
      shapeRendering="crispEdges"
      className="shrink-0 rounded bg-white"
    >
      <rect width={path.size} height={path.size} fill="#fff" />
      <path d={path.d} fill="#000" />
    </svg>
  )
}

/**
 * The seller's static Kaspi Pay QR on a printable invoice: Kaspi does not know the invoice, so the client types the
 * exact amount and the invoice number, and staff marks the invoice paid by hand. Light colours: it prints.
 */
export function StaticKaspiQr({ url, amount, number }: { url: string; amount: number; number: string }) {
  const path = useQrPath(url)
  return (
    <div className="mt-5 flex flex-wrap items-center gap-5 rounded border border-[#d9dde2] p-4 break-inside-avoid" data-testid="static-kaspi-qr">
      <QrSvg path={path} label="QR-код Kaspi для оплаты счёта" size={150} />
      <div className="min-w-0 flex-1 text-[13px] text-[#2f353b]">
        <p className="text-[15px] font-bold">Оплата через Kaspi по QR</p>
        <ol className="mt-1.5 list-decimal space-y-0.5 pl-5">
          <li>Отсканируйте QR в приложении Kaspi.kz или камерой телефона.</li>
          <li>
            Введите сумму ровно <b className="tabular-nums">{money(amount)}</b>.
          </li>
          <li>
            В комментарии укажите <b>счёт № {number}</b>.
          </li>
        </ol>
        <p className="mt-1.5 text-[#565e67]">Оплату подтверждаем вручную в рабочее время, после этого придёт письмо.</p>
        <a href={url} className="mt-1.5 inline-flex items-center gap-1.5 font-semibold text-[#3f6ad8] print:hidden">
          Открыть в Kaspi <ExternalLink className="size-4" aria-hidden />
        </a>
      </div>
    </div>
  )
}

function PaymentQrView({ url, amount, number, path }: { url: string; amount?: number | null; number?: string | null; path: { d: string; size: number } }) {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-4 rounded-nx border border-line bg-raised p-4" data-testid="payment-qr">
      <QrSvg path={path} label="QR-код для оплаты в Kaspi" />
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
