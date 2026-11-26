import { useEffect, useState, type ReactNode } from 'react'
import { Coins, CreditCard, FileText, FlaskConical, QrCode } from 'lucide-react'
import { api, FREE_CODE, goToCheckout, loadPublicConfig, money, type ClientMe, type PublicConfig, type Tariff } from '../api'
import { Alert, Button, cx } from '../components/ui'

type Method = 'invoice' | 'online'

interface Props {
  current?: string | null
  onChosen: (me: ClientMe) => void
  /** a Free account moving to a paid tariff: Free is not offered */
  upgrade?: boolean
}

export const FREE_FULL = 'Тестовые места закончились, выберите тариф или напишите менеджеру'

/** Tariff cards (paid, then the muted Free test) and the payment method: online (when a provider is configured)
 * or an invoice issued by a manager. Free is connected at once, without payment. */
export function TariffPicker({ current, onChosen, upgrade = false }: Props) {
  const [tariffs, setTariffs] = useState<Tariff[] | null>(null)
  const [config, setConfig] = useState<PublicConfig | null>(null)
  const [selected, setSelected] = useState<string | null>(current && current !== FREE_CODE ? current : null)
  const [method, setMethod] = useState<Method>('invoice')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .get<Tariff[]>('/api/tariffs')
      .then(setTariffs)
      .catch((err) => setError(err instanceof Error ? err.message : 'Не удалось загрузить тарифы'))
    loadPublicConfig()
      .then(setConfig)
      .catch(() => undefined)
  }, [])

  const online = !!config?.online_payments
  const paid = (tariffs ?? []).filter((t) => t.code !== FREE_CODE)
  const free = upgrade ? undefined : tariffs?.find((t) => t.code === FREE_CODE)
  const isFree = selected === FREE_CODE
  const chosenMethod: Method = online ? method : 'invoice'

  async function submit() {
    if (!selected) return
    setBusy(true)
    setError(null)
    try {
      const me = await api.post<ClientMe>('/api/me/tariff', isFree ? { tariff: selected } : { tariff: selected, method: chosenMethod })
      if (!isFree && chosenMethod === 'online' && me.payment_url && config?.online_mode !== 'qr') {
        goToCheckout(me.payment_url)
        return
      }
      onChosen(me)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось выбрать тариф')
    } finally {
      setBusy(false)
    }
  }

  const label = isFree
    ? 'Подключить бесплатный тест'
    : chosenMethod === 'online'
      ? 'Перейти к оплате'
      : upgrade
        ? 'Запросить счёт у менеджера'
        : current
          ? 'Сменить тариф'
          : 'Запросить счёт у менеджера'

  const chosen = paid.find((t) => t.code === selected)

  return (
    <div>
      <div className="grid gap-3 md:grid-cols-3" role="radiogroup" aria-label="Тариф">
        {paid.map((t) => {
          const active = selected === t.code
          return (
            <button
              key={t.code}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => setSelected(t.code)}
              className="nx-choice flex flex-col p-3.5 text-left md:p-4"
            >
              <span className="flex items-center justify-between gap-2">
                <span className="text-[15px] font-extrabold text-ink">{t.name}</span>
                <span className="nx-dot" aria-hidden />
              </span>
              <span className="mt-1.5 flex items-baseline gap-1.5 md:mt-3">
                <span className="text-[21px] leading-none font-extrabold md:text-[24px] tracking-[-0.02em] text-ink tabular-nums">{money(t.price_kzt)}</span>
                <span className="text-[13px] text-ink-3">в месяц</span>
              </span>
              <span className="mt-2 inline-flex items-center gap-1.5 text-[13.5px] font-semibold text-ink-2 md:mt-3">
                <Coins className="size-4 text-accent" aria-hidden />
                <span className="tabular-nums">{t.coins.toLocaleString('ru-RU')}</span> коинов в месяц
              </span>
              {t.description && <span className="mt-2 border-t border-line pt-2 text-[13px] leading-snug text-ink-3">{t.description}</span>}
            </button>
          )
        })}
      </div>
      {free && (
        <button
          type="button"
          role="radio"
          aria-checked={isFree}
          aria-label={`${free.name}: бесплатно`}
          disabled={free.available === false}
          onClick={() => setSelected(FREE_CODE)}
          className="nx-choice mt-3 flex w-full items-center gap-3.5 border-dashed px-4 py-3.5 text-left disabled:cursor-not-allowed disabled:opacity-60 aria-checked:border-solid"
        >
          <span className="grid size-9 flex-none place-items-center rounded-nx bg-ok-soft text-ok">
            <FlaskConical className="size-[18px]" aria-hidden />
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-baseline gap-x-2">
              <span className="text-[14.5px] font-bold text-ink">{free.name}</span>
              <span className="text-[13px] font-semibold text-ok">
                бесплатно{config?.free_days ? ` · ${config.free_days} дней` : ''}
              </span>
            </span>
            <span className="mt-0.5 block text-[13px] text-ink-3">
              {free.available === false
                ? FREE_FULL
                : `Бот подключается сразу, без оплаты. ${free.coins.toLocaleString('ru-RU')} коинов на весь тест.`}
            </span>
          </span>
          <span className="nx-dot" aria-hidden />
        </button>
      )}

      {selected && !isFree && (
        <fieldset className="mt-6">
          <legend className="text-[14px] font-bold text-ink">Способ оплаты</legend>
          <div className="mt-2.5 grid gap-3 sm:grid-cols-2" role="radiogroup" aria-label="Способ оплаты">
            {online && (
              <MethodCard
                active={chosenMethod === 'online'}
                onClick={() => setMethod('online')}
                icon={config?.online_mode === 'qr' ? <QrCode className="size-5" aria-hidden /> : <CreditCard className="size-5" aria-hidden />}
                title="Оплатить онлайн"
                text={
                  config?.online_mode === 'qr'
                    ? 'Kaspi QR: оплата в приложении Kaspi, бот создаётся сразу после оплаты'
                    : config?.online_methods.includes('kaspi_qr')
                      ? 'Картой или Kaspi QR, бот создаётся сразу после оплаты'
                      : 'Картой, бот создаётся сразу после оплаты'
                }
              />
            )}
            <MethodCard
              active={chosenMethod === 'invoice'}
              onClick={() => setMethod('invoice')}
              icon={<FileText className="size-5" aria-hidden />}
              title="Оплата по счёту через менеджера"
              text="Менеджер свяжется с вами и выставит счёт для оплаты банковским переводом"
            />
          </div>
        </fieldset>
      )}

      {error && (
        <div className="mt-4">
          <Alert>{error}</Alert>
        </div>
      )}
      <div className="mt-6 flex flex-col gap-3 border-t border-line pt-5 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-[13.5px] text-ink-3" aria-live="polite">
          {isFree && free
            ? `${free.name}: 0 ₸`
            : chosen
              ? <>Тариф «{chosen.name}»: <span className="font-bold text-ink tabular-nums">{money(chosen.price_kzt)}</span> в месяц</>
              : 'Выберите тариф, чтобы продолжить'}
        </p>
        <Button variant="primary" loading={busy} disabled={!selected} onClick={submit} className="w-full sm:w-auto">
          {label}
        </Button>
      </div>
    </div>
  )
}

function MethodCard({ active, onClick, icon, title, text }: { active: boolean; onClick: () => void; icon: ReactNode; title: string; text: string }) {
  return (
    <button type="button" role="radio" aria-checked={active} onClick={onClick} className="nx-choice flex gap-3 p-3.5 text-left">
      <span className={cx('mt-0.5 transition-colors', active ? 'text-accent' : 'text-ink-3')}>{icon}</span>
      <span className="min-w-0 flex-1">
        <span className="block text-[14px] font-bold text-ink">{title}</span>
        <span className="mt-0.5 block text-[13px] leading-snug text-ink-3">{text}</span>
      </span>
      <span className="nx-dot mt-0.5" aria-hidden />
    </button>
  )
}
