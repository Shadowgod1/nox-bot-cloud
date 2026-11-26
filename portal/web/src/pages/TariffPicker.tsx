import { useEffect, useState, type ReactNode } from 'react'
import { Check, CreditCard, FileText } from 'lucide-react'
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

  return (
    <div>
      <div className="grid gap-3 sm:grid-cols-3" role="radiogroup" aria-label="Тариф">
        {paid.map((t) => {
          const active = selected === t.code
          return (
            <button
              key={t.code}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => setSelected(t.code)}
              className={cx(
                'flex flex-col rounded-nx border p-4 text-left transition-colors',
                active ? 'border-accent bg-accent-soft' : 'border-line-strong bg-raised hover:border-accent',
              )}
            >
              <span className="flex items-center justify-between">
                <span className="text-[15px] font-extrabold text-ink">{t.name}</span>
                {active && <Check className="size-4 text-accent" aria-hidden />}
              </span>
              <span className="mt-2 text-[20px] font-extrabold text-ink tabular-nums">{money(t.price_kzt)}</span>
              <span className="text-[12.5px] text-ink-3">в месяц</span>
              <span className="mt-2 text-[13px] font-semibold text-ink-2">{t.coins.toLocaleString('ru-RU')} коинов</span>
              <span className="mt-1 text-[12.5px] text-ink-3">{t.description}</span>
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
          className={cx(
            'mt-3 flex w-full flex-wrap items-baseline gap-x-3 gap-y-1 rounded-nx border border-dashed px-4 py-3 text-left transition-colors',
            'disabled:cursor-not-allowed disabled:opacity-70',
            isFree ? 'border-accent bg-accent-soft' : 'border-line bg-sunken hover:border-line-strong',
          )}
        >
          <span className="text-[14px] font-bold text-ink-2">{free.name}</span>
          <span className="text-[13px] font-semibold text-ink-3 tabular-nums">0 ₸ · {free.coins.toLocaleString('ru-RU')} коинов</span>
          <span className="text-[12.5px] text-ink-3">
            {free.available === false
              ? FREE_FULL
              : `${free.description}: для теста, бот подключается сразу${config?.free_days ? `, ${config.free_days} дней` : ''}`}
          </span>
        </button>
      )}

      {selected && !isFree && (
        <fieldset className="mt-4">
          <legend className="text-[13px] font-semibold text-ink-2">Способ оплаты</legend>
          <div className="mt-1.5 grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Способ оплаты">
            {online && (
              <MethodCard
                active={chosenMethod === 'online'}
                onClick={() => setMethod('online')}
                icon={<CreditCard className="size-5" aria-hidden />}
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
        <div className="mt-3">
          <Alert>{error}</Alert>
        </div>
      )}
      <Button className="mt-4" variant="primary" loading={busy} disabled={!selected} onClick={submit}>
        {label}
      </Button>
    </div>
  )
}

function MethodCard({ active, onClick, icon, title, text }: { active: boolean; onClick: () => void; icon: ReactNode; title: string; text: string }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      onClick={onClick}
      className={cx(
        'flex gap-3 rounded-nx border p-3 text-left transition-colors',
        active ? 'border-accent bg-accent-soft' : 'border-line-strong bg-raised hover:border-accent',
      )}
    >
      <span className={active ? 'text-accent' : 'text-ink-3'}>{icon}</span>
      <span>
        <span className="block text-[14px] font-bold text-ink">{title}</span>
        <span className="block text-[12.5px] text-ink-3">{text}</span>
      </span>
    </button>
  )
}
