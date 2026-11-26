import { CircleAlert, CircleCheck, CloudOff, ExternalLink, TrendingUp } from 'lucide-react'
import type { DashboardCoins } from '../../api/types'
import { useBrand } from '../../brand'
import { Badge, Panel, cx } from '../../components/ui'
import { plural } from '../../i18n/ru'
import { coinsForecast, coinsLevel, formatDayMonth, formatNumber, formatPercent, formatTime, modelLabel, type Level } from './health'

const barTone: Record<Level, string> = { ok: 'bg-accent', warn: 'bg-warn', danger: 'bg-danger' }
const forecastTone: Record<Level, string> = {
  ok: 'bg-accent-soft text-accent-ink',
  warn: 'bg-warn-soft text-warn',
  danger: 'bg-danger-soft text-danger',
}
const badgeTone = { ok: 'neutral', warn: 'warn', danger: 'danger' } as const

const coinWord = (n: number) => plural(Math.round(n), ['коин', 'коина', 'коинов'])

export function CoinsCard({ coins }: { coins: DashboardCoins }) {
  const hasLimit = coins.limit > 0
  const level = coinsLevel(coins)
  const forecast = coinsForecast(coins)
  const percent = coins.percent ?? (hasLimit ? (coins.spent / coins.limit) * 100 : 0)
  const remaining = coins.remaining ?? Math.max(0, coins.limit - coins.spent)
  const brand = useBrand()
  const gateway = coins.source === 'gateway'
  const description = gateway
    ? coins.period_end
      ? `Баланс на период до ${formatDayMonth(coins.period_end)}.`
      : 'Баланс коинов на текущий период.'
    : 'Расход на работу модели в этом месяце.'

  return (
    <Panel
      title={`Коины ${brand.name}`}
      description={description}
      actions={
        hasLimit || coins.topup_url ? (
          <div className="flex flex-wrap items-center gap-2">
            {hasLimit && <Badge tone={badgeTone[level]}>израсходовано {formatPercent(percent)}</Badge>}
            {coins.topup_url && (
              <a
                href={coins.topup_url}
                target="_blank"
                rel="noopener noreferrer"
                data-testid="coins-topup"
                className="inline-flex h-8 items-center gap-1.5 rounded-[7px] border border-transparent bg-accent-solid px-2.5 text-[13px] font-medium whitespace-nowrap text-white transition-[background-color] duration-150 ease-out hover:bg-accent-hover"
              >
                Пополнить
                <ExternalLink className="size-3.5" aria-hidden />
              </a>
            )}
          </div>
        ) : undefined
      }
      bodyClassName="p-0"
    >
      <div className="px-5 pt-4 pb-5">
        {gateway && coins.stale && (
          <p data-testid="coins-stale" className="mb-3 flex items-start gap-2 text-[13px] text-ink-2">
            <CloudOff className="mt-0.5 size-4 shrink-0 text-ink-3" aria-hidden />
            <span>
              {coins.balance_at
                ? `Не удалось обновить баланс — показано значение на ${formatTime(coins.balance_at)}.`
                : 'Баланс пока недоступен — показан только расход этого бота.'}
            </span>
          </p>
        )}
        {hasLimit ? (
          <>
            <p className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5" data-testid="coins-remaining">
              <span className="text-[13px] text-ink-2">осталось</span>
              <span className={cx('tabular text-[30px] font-semibold leading-none', level === 'danger' ? 'text-danger' : 'text-ink')}>
                {formatNumber(remaining)}
              </span>
              <span className="tabular text-sm text-ink-2">из {formatNumber(coins.limit)}</span>
            </p>
            <div
              role="progressbar"
              aria-label="Израсходовано коинов за месяц"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(Math.min(100, percent))}
              className="mt-3.5 h-2 overflow-hidden rounded-full bg-sunken"
            >
              <div
                className={cx('h-full rounded-full transition-[width] duration-500 ease-out', barTone[level])}
                style={{ width: `${Math.min(100, Math.max(percent > 0 ? 1.5 : 0, percent))}%` }}
              />
            </div>
            <p className="tabular mt-1.5 text-[13px] text-ink-2">
              израсходовано {formatNumber(coins.spent)} {coinWord(coins.spent)}
            </p>
          </>
        ) : (
          <>
            <p className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5" data-testid="coins-spent">
              <span className="text-[13px] text-ink-2">израсходовано</span>
              <span className="tabular text-[30px] font-semibold leading-none text-ink">{formatNumber(coins.spent)}</span>
              <span className="text-sm text-ink-2">{coinWord(coins.spent)}</span>
            </p>
            {!gateway && <p className="mt-2 text-[13px] text-ink-3">Лимит не задан</p>}
          </>
        )}

        {forecast && (
          <p
            data-testid="coins-forecast"
            data-level={forecast.level}
            className={cx('mt-4 flex items-start gap-2 rounded-[8px] px-3 py-2.5 text-sm font-medium', forecastTone[forecast.level])}
          >
            {forecast.level === 'ok' ? (
              <CircleCheck className="mt-0.5 size-4 shrink-0" aria-hidden />
            ) : (
              <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
            )}
            <span>{forecast.text}</span>
          </p>
        )}

        {coins.unpriced_models.length > 0 && (
          <p data-testid="coins-unpriced" className="mt-3 text-[12px] text-ink-3">
            {coins.unpriced_models.length === 1 ? 'Для модели' : 'Для моделей'}{' '}
            {coins.unpriced_models.map(modelLabel).join(', ')} нет цены — расход посчитан по максимальному
            тарифу, уточните у {brand.company}.
          </p>
        )}
      </div>

      <div className="grid border-t border-line md:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] md:divide-x md:divide-line">
        <DailyChart coins={coins} />
        <ByModel coins={coins} />
      </div>

      <p className="border-t border-line px-5 py-3 text-[12px] text-ink-3">
        {gateway
          ? `1 коин ≈ $0.001 расходов на модель. Расход по дням и моделям — по данным этого бота.${
              coins.topup_url ? '' : ` Чтобы пополнить баланс, обратитесь в ${brand.company}.`
            }`
          : `1 коин ≈ $0.001 расходов на модель. Чтобы увеличить лимит, обратитесь в ${brand.company}.`}
      </p>
    </Panel>
  )
}

function DailyChart({ coins }: { coins: DashboardCoins }) {
  const byDate = coins.days
  const slots = Math.max(coins.days_in_month, byDate.length)
  const peak = Math.max(coins.avg_per_day, ...byDate.map((d) => d.coins), 1) * 1.1
  const avgPct = (coins.avg_per_day / peak) * 100
  const todayIdx = byDate.length - 1

  return (
    <div className="px-5 py-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 className="text-[13px] font-medium text-ink">Расход по дням</h3>
        <span className="flex items-center gap-1.5 text-[12px] text-ink-2">
          <span aria-hidden className="inline-block w-3.5 border-t border-dashed border-ink-3" />
          в среднем ~{formatNumber(coins.avg_per_day)} в день
        </span>
      </div>
      <div
        role="img"
        aria-label={`Расход коинов по дням: в среднем ${formatNumber(coins.avg_per_day)} в день, ${byDate.length} из ${slots} дней месяца`}
        className="relative h-24"
      >
        <div className="absolute inset-0 flex items-end gap-[2px]">
          {Array.from({ length: slots }, (_, i) => {
            const day = byDate[i]
            if (!day) {
              return <div key={i} className="h-1 min-w-0 flex-1 rounded-[2px] bg-sunken" />
            }
            const h = (day.coins / peak) * 100
            return (
              <div
                key={day.date}
                title={`${formatDayMonth(day.date)}: ${formatNumber(day.coins)} ${coinWord(day.coins)}`}
                className={cx(
                  'min-w-0 flex-1 rounded-t-[2px]',
                  day.coins === 0 ? 'bg-line' : i === todayIdx ? 'bg-accent' : 'bg-accent/45',
                )}
                style={{ height: `${Math.max(day.coins > 0 ? 3 : 2, h)}%` }}
              />
            )
          })}
        </div>
        {coins.avg_per_day > 0 && (
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-0 border-t border-dashed border-ink-3"
            style={{ bottom: `${avgPct}%` }}
          />
        )}
      </div>
      <div aria-hidden className="tabular mt-1.5 flex justify-between text-[11px] text-ink-3">
        <span>1</span>
        <span>{Math.ceil(slots / 2)}</span>
        <span>{slots}</span>
      </div>
    </div>
  )
}

function ByModel({ coins }: { coins: DashboardCoins }) {
  const items = [...coins.by_model].sort((a, b) => b.coins - a.coins)
  const total = items.reduce((a, m) => a + m.coins, 0)

  return (
    <div className="border-t border-line px-5 py-4 md:border-t-0">
      <h3 className="mb-3 flex items-center gap-1.5 text-[13px] font-medium text-ink">
        <TrendingUp className="size-3.5 text-ink-3" aria-hidden />
        По моделям
      </h3>
      {items.length === 0 ? (
        <p className="text-[13px] text-ink-3">В этом месяце расхода ещё не было.</p>
      ) : (
        <ul className="space-y-3">
          {items.map((m) => {
            const share = total > 0 ? (m.coins / total) * 100 : 0
            return (
              <li key={m.model}>
                <div className="flex items-baseline justify-between gap-3 text-[13px]">
                  <span className="min-w-0 truncate text-ink">{modelLabel(m.model)}</span>
                  <span className="tabular shrink-0 text-ink-2">
                    {formatNumber(m.coins)} · {formatPercent(share)}
                  </span>
                </div>
                <div className="mt-1 h-1 overflow-hidden rounded-full bg-sunken">
                  <div className="h-full rounded-full bg-ink-3" style={{ width: `${share}%` }} />
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
