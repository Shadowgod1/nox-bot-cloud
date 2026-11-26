import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { ArrowLeft, Printer } from 'lucide-react'
import { api, ApiError, money, type Invoice } from '../api'
import { Alert, Button } from '../components/ui'

/**
 * Printable invoice (A4). "Печать / PDF" uses the browser's print dialog, where "Save as PDF" makes the file.
 * /invoice — the client's own invoice; /staff/applications/:id/invoice — the same page for staff.
 */
export function InvoicePage() {
  const { id, number } = useParams()
  const navigate = useNavigate()
  const [inv, setInv] = useState<Invoice | null>(null)
  const [error, setError] = useState<string | null>(null)
  const back = id ? `/staff/applications/${id}` : '/'

  useEffect(() => {
    api
      .get<Invoice>(
        id
          ? number
            ? `/api/staff/applications/${id}/invoices/${encodeURIComponent(number)}`
            : `/api/staff/applications/${id}/invoice`
          : number
            ? `/api/me/invoices/${encodeURIComponent(number)}`
            : '/api/me/invoice',
      )
      .then(setInv)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) navigate(id ? '/staff/login' : '/login', { replace: true })
        else setError(err instanceof Error ? err.message : 'Не удалось загрузить счёт')
      })
  }, [id, number, navigate])

  const s = inv?.seller
  const issued = inv ? new Date(inv.issued_at).toLocaleDateString('ru-RU', { timeZone: 'Asia/Almaty' }) : ''

  return (
    <div className="min-h-dvh bg-canvas px-4 py-6 print:bg-white print:p-0">
      <div className="mx-auto flex max-w-[800px] items-center gap-3 print:hidden">
        <Link to={back} className="inline-flex items-center gap-1 text-[13.5px] font-semibold text-ink-2 hover:text-accent">
          <ArrowLeft className="size-4" aria-hidden /> Назад
        </Link>
        {inv && (
          <Button className="ml-auto" variant="primary" icon={<Printer className="size-4" aria-hidden />} onClick={() => window.print()}>
            Печать / PDF
          </Button>
        )}
      </div>
      {error && (
        <div className="mx-auto mt-4 max-w-[800px]">
          <Alert>{error}</Alert>
        </div>
      )}
      {inv && s && (
        <article className="mx-auto mt-4 max-w-[800px] bg-white p-10 text-[13.5px] leading-relaxed text-[#2f353b] shadow-sm print:mt-0 print:shadow-none">
          <header className="flex items-start justify-between gap-6 border-b border-[#d9dde2] pb-5">
            <div>
              <div className="text-[20px] font-extrabold text-[#3f6ad8]">{s.name}</div>
              {s.address && <div className="text-[#565e67]">{s.address}</div>}
              {s.phone && <div className="text-[#565e67]">{s.phone}</div>}
            </div>
            <div className="text-right">
              <h1 className="text-[22px] font-extrabold">Счёт на оплату № {inv.number}</h1>
              <div className="text-[#565e67]">от {issued}</div>
              {inv.paid && <div className="mt-1 font-bold text-[#2e7d4f]">ОПЛАЧЕН</div>}
            </div>
          </header>

          <table className="mt-5 w-full border-collapse text-[13px]">
            <tbody>
              {[
                ['Бенефициар', s.name],
                ['БИН', s.bin],
                ['Банк бенефициара', s.bank],
                ['ИИК', s.iik],
                ['БИК', s.bik],
                ['Кбе', s.kbe],
              ].map(([k, v]) => (
                <tr key={k}>
                  <td className="w-[200px] border border-[#d9dde2] px-3 py-1.5 text-[#565e67]">{k}</td>
                  <td className="border border-[#d9dde2] px-3 py-1.5 font-semibold">{v || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="mt-5">
            <div>
              <span className="text-[#565e67]">Покупатель: </span>
              <b>{inv.buyer_company}</b>
            </div>
            <div className="text-[#565e67]">
              {inv.buyer_contact}, {inv.buyer_phone}, {inv.buyer_email}
            </div>
          </div>

          <table className="mt-5 w-full border-collapse text-[13px]">
            <thead>
              <tr className="bg-[#f4f5f7]">
                <th className="border border-[#d9dde2] px-3 py-2 text-left">№</th>
                <th className="border border-[#d9dde2] px-3 py-2 text-left">Наименование</th>
                <th className="border border-[#d9dde2] px-3 py-2 text-right">Кол-во</th>
                <th className="border border-[#d9dde2] px-3 py-2 text-right">Сумма</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="border border-[#d9dde2] px-3 py-2">1</td>
                <td className="border border-[#d9dde2] px-3 py-2">
                  {inv.kind === 'topup' ? (
                    <>
                      Пакет коинов для WhatsApp-ассистента Nox Bot: {inv.tariff.coins.toLocaleString('ru-RU')} коинов (не сгорают в
                      конце месяца). Адрес: {inv.bot_host}
                    </>
                  ) : (
                    <>
                      Абонентское обслуживание WhatsApp-ассистента Nox Bot, тариф «{inv.tariff.name}» (
                      {inv.tariff.coins.toLocaleString('ru-RU')} коинов), 1 месяц
                      {inv.period_start && inv.period_end ? ` (${new Date(inv.period_start).toLocaleDateString('ru-RU', { timeZone: 'Asia/Almaty' })} – ${new Date(inv.period_end).toLocaleDateString('ru-RU', { timeZone: 'Asia/Almaty' })})` : ''}
                      . Адрес: {inv.bot_host}
                    </>
                  )}
                </td>
                <td className="border border-[#d9dde2] px-3 py-2 text-right">1</td>
                <td className="border border-[#d9dde2] px-3 py-2 text-right tabular-nums">{money(inv.amount_kzt)}</td>
              </tr>
            </tbody>
            <tfoot>
              <tr>
                <td colSpan={3} className="border border-[#d9dde2] px-3 py-2 text-right font-bold">
                  Итого к оплате
                </td>
                <td className="border border-[#d9dde2] px-3 py-2 text-right font-extrabold tabular-nums">{money(inv.amount_kzt)}</td>
              </tr>
            </tfoot>
          </table>

          <p className="mt-5 text-[#565e67]">
            Оплатите счёт в течение {inv.due_days} рабочих дней. В назначении платежа укажите: «Оплата по счёту № {inv.number}».
            {inv.kind === 'topup'
              ? 'Коины будут начислены на баланс сразу после поступления оплаты.'
              : inv.kind === 'renewal'
                ? 'После поступления оплаты начнётся следующий месяц обслуживания с новым пакетом коинов.'
                : 'После поступления оплаты бот будет создан, а на e-mail придёт ссылка на панель управления.'}
          </p>
          <footer className="mt-10 flex justify-between border-t border-[#d9dde2] pt-4 text-[#667079]">
            <span>{s.name}</span>
            <span>Подпись ______________________</span>
          </footer>
        </article>
      )}
    </div>
  )
}
