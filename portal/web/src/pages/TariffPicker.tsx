import { useEffect, useState } from 'react'
import { Check } from 'lucide-react'
import { api, money, type ClientMe, type Tariff } from '../api'
import { Alert, Button, cx } from '../components/ui'

interface Props {
  current?: string | null
  onChosen: (me: ClientMe) => void
}

/** Tariff cards; choosing one issues (or re-issues) the invoice. */
export function TariffPicker({ current, onChosen }: Props) {
  const [tariffs, setTariffs] = useState<Tariff[] | null>(null)
  const [selected, setSelected] = useState<string | null>(current ?? null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .get<Tariff[]>('/api/tariffs')
      .then(setTariffs)
      .catch((err) => setError(err instanceof Error ? err.message : 'Не удалось загрузить тарифы'))
  }, [])

  async function submit() {
    if (!selected) return
    setBusy(true)
    setError(null)
    try {
      onChosen(await api.post<ClientMe>('/api/me/tariff', { tariff: selected }))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось выбрать тариф')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <div className="grid gap-3 sm:grid-cols-3" role="radiogroup" aria-label="Тариф">
        {tariffs?.map((t) => {
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
      {error && (
        <div className="mt-3">
          <Alert>{error}</Alert>
        </div>
      )}
      <Button className="mt-4" variant="primary" loading={busy} disabled={!selected || selected === current} onClick={submit}>
        {current ? 'Сменить тариф и обновить счёт' : 'Выбрать тариф и получить счёт'}
      </Button>
    </div>
  )
}
