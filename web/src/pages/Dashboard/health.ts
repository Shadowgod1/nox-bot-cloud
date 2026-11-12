// Pure helpers for the dashboard: bot status derivation and ru-RU formatting.
import type { Dashboard, DashboardCoins } from '../../api/types'
import { plural } from '../../i18n/ru'
import { CLAUDE_MODELS } from '../System/models'

export type Level = 'ok' | 'warn' | 'danger'

export interface BotStatus {
  level: Level
  title: string
  /** Why the status is not green, most important first. Empty when everything is fine. */
  reasons: string[]
}

/** "Бот работает?" — one state for the whole bot, worst reason wins. */
export function deriveStatus(d: Pick<Dashboard, 'whatsapp' | 'bot_enabled' | 'errors_24h' | 'coins'>): BotStatus {
  const red: string[] = []
  if (d.whatsapp.status === 'degraded') red.push('WhatsApp подключён, но сообщения не отправляются')
  else if (d.whatsapp.status !== 'open') red.push('WhatsApp не подключён — бот не видит сообщения клиентов')
  if (d.coins.exhausted) red.push('Лимит коинов исчерпан — бот передаёт сообщения оператору')
  if (!d.bot_enabled) red.push('Бот выключен — отвечают только операторы')
  if (red.length) return { level: 'danger', title: 'Бот не отвечает', reasons: red }

  const amber: string[] = []
  const errors = d.errors_24h.llm + d.errors_24h.send
  if (errors > 0) amber.push(`${errors} ${plural(errors, ['ошибка', 'ошибки', 'ошибок'])} за последние 24 часа`)
  const c = d.coins
  if (c.limit > 0) {
    if ((c.percent ?? 0) >= 80) amber.push(`Израсходовано ${formatPercent(c.percent ?? 0)} месячного лимита коинов`)
    if (c.runs_out_on) amber.push(`При текущем расходе коинов хватит только до ${formatDayMonth(c.runs_out_on)}`)
  }
  if (amber.length) return { level: 'warn', title: 'Бот работает, но требует внимания', reasons: amber }

  return { level: 'ok', title: 'Бот работает и отвечает клиентам', reasons: [] }
}

/** Threshold color for the coins bar: <80 neutral, ≥80 amber, ≥100 (or exhausted) red. */
export function coinsLevel(c: DashboardCoins): Level {
  if (c.limit <= 0) return 'ok'
  const p = c.percent ?? 0
  if (c.exhausted || p >= 100) return 'danger'
  if (p >= 80) return 'warn'
  return 'ok'
}

export interface Forecast {
  level: Level
  text: string
}

/** The key message of the coins card: will the limit last until the month's end? */
export function coinsForecast(c: DashboardCoins): Forecast | null {
  if (c.limit <= 0) return null
  if (c.exhausted) {
    return { level: 'danger', text: 'Лимит на этот месяц исчерпан. Бот передаёт новые сообщения оператору до начала следующего месяца.' }
  }
  const pace = `При текущем расходе (~${formatNumber(c.avg_per_day)} в день)`
  if (c.runs_out_on) {
    const level: Level = daysUntil(c.runs_out_on) <= 3 ? 'danger' : 'warn'
    return { level, text: `${pace} коинов хватит до ${formatDayMonth(c.runs_out_on)}` }
  }
  const leftover = Math.max(0, c.limit - c.projected_month)
  return { level: 'ok', text: `${pace} коинов хватит до конца месяца, останется ~${formatNumber(leftover)}` }
}

/* ---------- formatting ---------- */

export function formatNumber(n: number): string {
  return Math.round(n).toLocaleString('ru-RU')
}

/** 41.3 -> "41%", 0.5 -> "0,5%". */
export function formatPercent(p: number): string {
  return `${p > 0 && p < 1 ? p.toLocaleString('ru-RU', { maximumFractionDigits: 1 }) : Math.round(p)}%`
}

/** "2026-11-27" -> "27 ноября" (parsed as a local calendar date, no TZ shift). */
export function formatDayMonth(ymd: string): string {
  const d = parseYmd(ymd)
  return d ? new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' }).format(d) : ymd
}

/** 2300 -> "~2,3 с". */
export function formatSeconds(ms: number): string {
  return `~${(ms / 1000).toLocaleString('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} с`
}

/** Relative Russian time: "только что", "5 минут назад", "2 часа назад", "3 дня назад". */
export function formatAgo(iso: string, now: number = Date.now()): string {
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const min = Math.floor((now - t) / 60_000)
  if (min < 1) return 'только что'
  if (min < 60) return `${min} ${plural(min, ['минуту', 'минуты', 'минут'])} назад`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h} ${plural(h, ['час', 'часа', 'часов'])} назад`
  const days = Math.floor(h / 24)
  return `${days} ${plural(days, ['день', 'дня', 'дней'])} назад`
}

/** "claude-sonnet-5-5" -> "Claude Sonnet 5.5"; unknown ids are shown as is. */
export function modelLabel(id: string): string {
  const known = CLAUDE_MODELS.find(([m]) => m === id)
  return known ? known[1].split(' — ')[0] : id
}

function parseYmd(ymd: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(ymd)
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null
}

function daysUntil(ymd: string): number {
  const d = parseYmd(ymd)
  if (!d) return Infinity
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Math.round((d.getTime() - today.getTime()) / 86_400_000)
}
