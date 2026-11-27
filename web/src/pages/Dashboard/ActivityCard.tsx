import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react'
import { api } from '../../api/client'
import type { Activity, ActivityDay, ActivityTotals } from '../../api/types'
import { useLoad } from '../../hooks/useLoad'
import { ErrorState, Spinner, cx } from '../../components/ui'
import { plural } from '../../i18n/ru'

const PERIODS = [7, 14, 30] as const
type Period = (typeof PERIODS)[number]

const MONTHS = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']

function dayLabel(iso: string, long = false): string {
  const [, m, d] = iso.split('-').map(Number)
  return long ? `${d} ${MONTHS[m - 1]}` : `${d}.${String(m).padStart(2, '0')}`
}

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    setWidth(el.clientWidth)
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(([e]) => setWidth(Math.round(e.contentRect.width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, width]
}

/** A "nice" axis maximum and 3–4 gridline steps. */
function niceScale(max: number): { top: number; ticks: number[] } {
  if (max <= 0) return { top: 4, ticks: [0, 2, 4] }
  const raw = max / 3
  const pow = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 5, 10].map((k) => k * pow).find((s) => s >= raw) ?? raw
  const top = Math.ceil(max / step) * step
  const ticks: number[] = []
  for (let v = 0; v <= top + 1e-9; v += step) ticks.push(Math.round(v))
  return { top, ticks }
}

function formatSeconds(s: number | null): string {
  if (s === null) return '—'
  if (s < 60) return `${s} с`
  const m = Math.round(s / 60)
  return `${m} мин`
}

/** The whole overview activity block: KPIs with the trend, messages per day, the hours clients write. */
export function ActivityCard() {
  const [days, setDays] = useState<Period>(14)
  const { data, error, loading, reload } = useLoad(() => api.get<Activity>(`/api/dashboard/activity?days=${days}`, { silent: true }), [days])

  return (
    <div className="grid items-stretch gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
      <section aria-labelledby="act-h" className="flex flex-col rounded-[var(--radius-panel)] border border-line bg-surface">
        <header className="flex min-h-[57px] flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3">
          <h2 id="act-h" className="text-[15px] font-semibold">
            Активность
          </h2>
          <div role="radiogroup" aria-label="Период" className="inline-flex rounded-[8px] bg-sunken p-0.5 text-[13px]">
            {PERIODS.map((p) => (
              <button
                key={p}
                type="button"
                role="radio"
                aria-checked={days === p}
                onClick={() => setDays(p)}
                className={cx(
                  'rounded-[6px] px-3 py-1 font-medium transition-colors',
                  days === p ? 'bg-surface text-ink shadow-[0_1px_2px_rgba(0,0,0,0.08)]' : 'text-ink-2 hover:text-ink',
                )}
              >
                {p} дней
              </button>
            ))}
          </div>
        </header>
        {error && !data ? (
          <div className="p-5">
            <ErrorState message={error.message} onRetry={reload} />
          </div>
        ) : !data ? (
          <div className="p-5">
            <Spinner />
          </div>
        ) : (
          <div className={cx('flex flex-1 flex-col transition-opacity', loading && 'opacity-60')}>
            <Kpis totals={data.totals} previous={data.previous} median={data.median_reply_seconds} days={data.days} />
            <div className="flex-1 border-t border-line px-5 pt-4 pb-3">
              <DailyChart series={data.series} />
            </div>
          </div>
        )}
      </section>

      <section aria-labelledby="hours-h" className="flex flex-col rounded-[var(--radius-panel)] border border-line bg-surface">
        <header className="flex min-h-[57px] items-center border-b border-line px-5 py-3">
          <h2 id="hours-h" className="text-[15px] font-semibold">
            Когда пишут клиенты
          </h2>
        </header>
        <div className="flex flex-1 flex-col px-5 pt-4 pb-3">{data ? <HoursChart hours={data.hours} /> : <Spinner />}</div>
      </section>
    </div>
  )
}

function Trend({ now, before }: { now: number; before: number }) {
  if (before === 0 && now === 0) return null
  if (before === 0)
    return (
      <span className="inline-flex items-center gap-0.5 text-[12px] font-medium text-ink-3">
        <ArrowUpRight className="size-3.5" aria-hidden />
        новое
      </span>
    )
  const pct = Math.round(((now - before) / before) * 100)
  const Icon = pct > 0 ? ArrowUpRight : pct < 0 ? ArrowDownRight : Minus
  return (
    <span className="inline-flex items-center gap-0.5 text-[12px] font-medium text-ink-2" title="К предыдущему периоду такой же длины">
      <Icon className="size-3.5" aria-hidden />
      {pct > 0 ? '+' : ''}
      {pct}%
    </span>
  )
}

function Kpis({ totals, previous, median, days }: { totals: ActivityTotals; previous: ActivityTotals; median: number | null; days: number }) {
  const handoverShare = totals.conversations ? Math.round((totals.handovers / totals.conversations) * 100) : null
  const items = [
    {
      label: plural(totals.conversations, ['диалог', 'диалога', 'диалогов']),
      value: totals.conversations.toLocaleString('ru-RU'),
      trend: <Trend now={totals.conversations} before={previous.conversations} />,
    },
    {
      label: plural(totals.bookings, ['заявка', 'заявки', 'заявок']),
      value: totals.bookings.toLocaleString('ru-RU'),
      trend: <Trend now={totals.bookings} before={previous.bookings} />,
    },
    {
      label: 'передано оператору',
      value: handoverShare === null ? '—' : `${handoverShare}%`,
      trend: (
        <span className="text-[12px] text-ink-3">
          {totals.handovers} из {totals.conversations}
        </span>
      ),
    },
    {
      label: 'обычное время ответа',
      value: formatSeconds(median),
      trend: <span className="text-[12px] text-ink-3">медиана</span>,
    },
  ]
  return (
    <dl className="grid grid-cols-2 divide-line md:grid-cols-4 md:divide-x" aria-label={`Итоги за ${days} дней`}>
      {items.map((it) => (
        <div key={it.label} className="flex flex-col-reverse justify-end gap-1 px-5 py-4">
          <dt className="flex flex-wrap items-center gap-x-2 text-[13px] text-ink-2">
            {it.label}
            {it.trend}
          </dt>
          <dd className="tabular text-[26px] leading-none font-semibold text-ink">{it.value}</dd>
        </div>
      ))}
    </dl>
  )
}

const H = 210
const PAD = { top: 10, right: 4, bottom: 24, left: 34 }

function Legend() {
  return (
    <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12.5px] text-ink-2">
      <span className="inline-flex items-center gap-1.5">
        <span aria-hidden className="size-2.5 rounded-[3px] bg-[var(--chart-1)]" />
        Сообщения клиентов
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span aria-hidden className="size-2.5 rounded-[3px] bg-[var(--chart-2)]" />
        Ответы бота
      </span>
    </div>
  )
}

/** Top-rounded bar anchored to the baseline. */
function bar(x: number, y: number, w: number, h: number): string {
  if (h <= 0) return ''
  const r = Math.min(4, w / 2, h)
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`
}

function DailyChart({ series }: { series: ActivityDay[] }) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const empty = series.every((d) => d.client_messages === 0 && d.bot_replies === 0)
  const max = Math.max(...series.map((d) => Math.max(d.client_messages, d.bot_replies)), 0)
  const { top, ticks } = niceScale(max)
  const innerW = Math.max(width - PAD.left - PAD.right, 0)
  const innerH = H - PAD.top - PAD.bottom
  const slot = series.length ? innerW / series.length : 0
  const group = Math.min(slot * 0.7, 36)
  const barW = Math.max((group - 2) / 2, 1)
  const y = (v: number) => PAD.top + innerH - (v / top) * innerH
  const labelEvery = series.length > 14 ? 5 : series.length > 7 ? 2 : 1
  const hovered = hover !== null ? series[hover] : null

  return (
    <div>
      <Legend />
      <div ref={ref} className="relative" onMouseLeave={() => setHover(null)}>
        {width > 0 && (
          <svg width={width} height={H} role="img" aria-label="Сообщения клиентов и ответы бота по дням" className="block overflow-visible">
            {ticks.map((t) => (
              <g key={t}>
                <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} className="stroke-line" strokeWidth={1} />
                <text x={PAD.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="fill-ink-3 text-[11px] tabular-nums">
                  {t}
                </text>
              </g>
            ))}
            {series.map((d, i) => {
              const x0 = PAD.left + i * slot + (slot - group) / 2
              return (
                <g key={d.date}>
                  {hover === i && <rect x={PAD.left + i * slot} y={PAD.top} width={slot} height={innerH} className="fill-sunken" />}
                  <path d={bar(x0, y(d.client_messages), barW, y(0) - y(d.client_messages))} fill="var(--chart-1)" />
                  <path d={bar(x0 + barW + 2, y(d.bot_replies), barW, y(0) - y(d.bot_replies))} fill="var(--chart-2)" />
                  {(i % labelEvery === 0 || i === series.length - 1) && (
                    <text x={PAD.left + i * slot + slot / 2} y={H - 6} textAnchor="middle" className="fill-ink-3 text-[11px] tabular-nums">
                      {dayLabel(d.date)}
                    </text>
                  )}
                  <rect
                    x={PAD.left + i * slot}
                    y={PAD.top}
                    width={slot}
                    height={innerH + PAD.bottom}
                    fill="transparent"
                    onMouseEnter={() => setHover(i)}
                    onFocus={() => setHover(i)}
                    tabIndex={-1}
                  />
                </g>
              )
            })}
            <line x1={PAD.left} x2={width - PAD.right} y1={y(0)} y2={y(0)} className="stroke-line-strong" strokeWidth={1} />
          </svg>
        )}
        {empty && (
          <p className="pointer-events-none absolute inset-0 grid place-items-center text-center text-[13px] text-ink-3">
            Графики заполнятся, когда клиенты начнут писать в WhatsApp.
          </p>
        )}
        {hovered && hover !== null && !empty && (
          <div
            role="tooltip"
            className="pointer-events-none absolute top-0 z-10 w-[190px] rounded-[8px] border border-line bg-surface px-3 py-2.5 text-[12.5px] shadow-[var(--shadow-pop)]"
            style={{
              left: Math.min(Math.max(PAD.left + hover * slot + slot / 2 - 95, 0), Math.max(width - 190, 0)),
            }}
          >
            <p className="mb-1.5 font-semibold text-ink">{dayLabel(hovered.date, true)}</p>
            <TipRow swatch="var(--chart-1)" label="Сообщения клиентов" value={hovered.client_messages} />
            <TipRow swatch="var(--chart-2)" label="Ответы бота" value={hovered.bot_replies} />
            <div className="my-1.5 border-t border-line" />
            <TipRow label="Диалоги" value={hovered.conversations} />
            <TipRow label="Заявки" value={hovered.bookings} />
            <TipRow label="Оператору" value={hovered.handovers} />
          </div>
        )}
      </div>
      <table className="sr-only">
        <caption>Активность по дням</caption>
        <thead>
          <tr>
            <th>День</th>
            <th>Сообщения клиентов</th>
            <th>Ответы бота</th>
            <th>Диалоги</th>
            <th>Заявки</th>
          </tr>
        </thead>
        <tbody>
          {series.map((d) => (
            <tr key={d.date}>
              <td>{dayLabel(d.date, true)}</td>
              <td>{d.client_messages}</td>
              <td>{d.bot_replies}</td>
              <td>{d.conversations}</td>
              <td>{d.bookings}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function TipRow({ label, value, swatch }: { label: string; value: number; swatch?: string }) {
  return (
    <p className="flex items-center justify-between gap-3 py-0.5 text-ink-2">
      <span className="inline-flex items-center gap-1.5">
        {swatch && <span aria-hidden className="size-2 rounded-[2px]" style={{ background: swatch }} />}
        {label}
      </span>
      <span className="tabular font-semibold text-ink">{value}</span>
    </p>
  )
}

const HH = 150

function HoursChart({ hours }: { hours: number[] }) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const total = hours.reduce((a, b) => a + b, 0)
  const max = Math.max(...hours, 0)
  const peak = max > 0 ? hours.indexOf(max) : null
  const innerH = HH - 20
  const slot = width / 24
  const w = Math.max(slot - 2, 1)
  const night = hours.slice(0, 9).reduce((a, b) => a + b, 0) + hours.slice(21).reduce((a, b) => a + b, 0)

  useEffect(() => setHover(null), [hours])

  return (
    <div className="flex flex-1 flex-col">
      <p className="mb-3 text-[13px] text-ink-2">
        {total === 0
          ? 'Покажем, в какие часы клиенты пишут чаще всего.'
          : (
            <>
              Пик — <span className="font-semibold text-ink">{peak}:00–{(peak! + 1) % 24}:00</span>
              {night > 0 && (
                <>
                  , в нерабочее время (21:00–9:00) — {Math.round((night / total) * 100)}% сообщений
                </>
              )}
            </>
          )}
      </p>
      <div ref={ref} className="relative mt-auto" onMouseLeave={() => setHover(null)}>
        {width > 0 && (
          <svg width={width} height={HH} role="img" aria-label="Сообщения клиентов по часам суток" className="block">
            {hours.map((n, h) => {
              const bh = max ? (n / max) * innerH : 0
              return (
                <g key={h}>
                  <path
                    d={bar(h * slot + 1, innerH - bh, w, Math.max(bh, n > 0 ? 2 : 0))}
                    fill="var(--chart-1)"
                    opacity={hover === null || hover === h ? 1 : 0.45}
                  />
                  {n === 0 && <rect x={h * slot + 1} y={innerH - 1} width={w} height={1} className="fill-line-strong" />}
                  {h % 6 === 0 && (
                    <text
                      x={h === 0 ? 0 : h * slot + slot / 2}
                      y={HH - 4}
                      textAnchor={h === 0 ? 'start' : 'middle'}
                      className="fill-ink-3 text-[11px] tabular-nums"
                    >
                      {h}:00
                    </text>
                  )}
                  <rect x={h * slot} y={0} width={slot} height={HH} fill="transparent" onMouseEnter={() => setHover(h)} />
                </g>
              )
            })}
          </svg>
        )}
        {hover !== null && total > 0 && (
          <div
            role="tooltip"
            className="pointer-events-none absolute -top-2 z-10 rounded-[7px] border border-line bg-surface px-2.5 py-1.5 text-[12.5px] whitespace-nowrap shadow-[var(--shadow-pop)]"
            style={{ left: Math.min(Math.max(hover * slot + slot / 2 - 60, 0), Math.max(width - 120, 0)) }}
          >
            <span className="text-ink-2">
              {hover}:00–{(hover + 1) % 24}:00 ·{' '}
            </span>
            <span className="tabular font-semibold text-ink">{hours[hover]}</span>
          </div>
        )}
      </div>
    </div>
  )
}
