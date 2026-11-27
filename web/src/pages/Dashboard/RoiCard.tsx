import { useState } from 'react'
import { Link } from 'react-router'
import { FlaskConical } from 'lucide-react'
import { api } from '../../api/client'
import type { RoiStats } from '../../api/types'
import { useLoad } from '../../hooks/useLoad'
import { Segmented, Spinner } from '../../components/ui'
import { plural } from '../../i18n/ru'
import { coinsWord } from '../../components/PriceImport'
import { formatCoins, roundCoins } from './health'

type Period = '7' | '30'

/** "Что бот сделал": compact payback numbers for 7 / 30 days; an honest empty state for a new bot. */
export function RoiCard() {
  const [period, setPeriod] = useState<Period>('7')
  const { data, loading, error } = useLoad(
    () => api.get<RoiStats>(`/api/dashboard/roi?days=${period}`, { silent: true }),
    [period],
  )

  return (
    <section aria-labelledby="roi-h" className="rounded-[var(--radius-panel)] border border-line bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3">
        <h2 id="roi-h" className="text-[15px] font-semibold">
          Что сделал бот
        </h2>
        <Segmented<Period>
          label="Период"
          value={period}
          onChange={setPeriod}
          options={[
            { value: '7', label: '7 дней' },
            { value: '30', label: '30 дней' },
          ]}
        />
      </div>
      {loading && !data ? (
        <Spinner className="py-6" />
      ) : error && !data ? (
        <p className="px-5 py-4 text-sm text-ink-2">Статистика сейчас недоступна.</p>
      ) : data && !data.has_activity ? (
        <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
          <p className="max-w-[60ch] text-sm text-ink-2">
            Пока клиентов не было — здесь появится, сколько диалогов бот провёл сам, сколько заявок собрал и сколько раз ответил ночью.
            {data.coins > 0 && ` На проверку бота потрачено ${formatCoins(data.coins)} ${coinsWord(roundCoins(data.coins))}.`}
          </p>
          <Link to="/test-chat" className="inline-flex items-center gap-1.5 text-sm font-medium text-accent underline">
            <FlaskConical className="size-4" aria-hidden />
            Проверить бота
          </Link>
        </div>
      ) : data ? (
          <dl className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5" data-testid="roi-stats">
            <Num value={data.bot_conversations} label={plural(data.bot_conversations, ['диалог провёл сам', 'диалога провёл сам', 'диалогов провёл сам'])} />
            <Num value={data.bookings} label={plural(data.bookings, ['заявку собрал', 'заявки собрал', 'заявок собрал'])} />
            <Num
              value={data.after_hours_conversations}
              label="ответил в нерабочее время"
              hint={data.hours_source === 'default' ? 'с 21:00 до 09:00' : 'по часам из настроек'}
            />
            <Num value={data.handovers} label="передал администратору" />
            <Num value={formatCoins(data.coins)} label={`${coinsWord(roundCoins(data.coins))} потрачено`} />
          </dl>
      ) : null}
    </section>
  )
}

function Num({ value, label, hint }: { value: number | string; label: string; hint?: string }) {
  return (
    <div className="flex flex-col-reverse justify-end border-line px-5 py-3.5 [&:not(:last-child)]:border-b sm:[&:not(:last-child)]:border-b-0">
      <dt className="mt-1 text-[13px] leading-snug text-ink-2">
        {label}
        {hint && <span className="block text-[12px] text-ink-3">{hint}</span>}
      </dt>
      <dd className="tabular text-[20px] font-semibold leading-none text-ink">{value}</dd>
    </div>
  )
}
