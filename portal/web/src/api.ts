/**
 * Fetch wrapper for the portal API: cookies always included, mutations carry X-Nox-CSRF (= cookie
 * nox_csrf), errors `{error:{code,message}}` become ApiError with a Russian message.
 */

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

const MESSAGES: Record<string, string> = {
  invalid_credentials: 'Неверный e-mail или пароль.',
  rate_limited: 'Слишком много попыток. Повторите позже.',
  csrf_failed: 'Сессия устарела. Обновите страницу.',
  unauthorized: 'Требуется вход.',
  email_failed: 'Не удалось отправить письмо. Повторите попытку позже.',
}

export function getCookie(name: string): string {
  const prefix = `${name}=`
  for (const part of document.cookie.split(';')) {
    const c = part.trim()
    if (c.startsWith(prefix)) return decodeURIComponent(c.slice(prefix.length))
  }
  return ''
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const headers = new Headers()
  if (body !== undefined) headers.set('Content-Type', 'application/json')
  if (method !== 'GET') {
    const csrf = getCookie('nox_csrf')
    if (csrf) headers.set('X-Nox-CSRF', csrf)
  }
  let res: Response
  try {
    res = await fetch(url, {
      method,
      headers,
      credentials: 'include',
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'network', 'Нет связи с сервером. Проверьте интернет и повторите.')
  }
  if (res.status === 204) return undefined as T
  let data: unknown = null
  try {
    data = await res.json()
  } catch {
    data = null
  }
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string } } | null)?.error
    const code = err?.code ?? `http_${res.status}`
    let message = MESSAGES[code] ?? err?.message ?? `Ошибка сервера (${res.status}).`
    if (code === 'validation_error' && err?.message) message = `Проверьте поля: ${err.message}`
    if (res.status >= 502 && !err) message = 'Сервис временно недоступен. Повторите через минуту.'
    throw new ApiError(res.status, code, message)
  }
  return data as T
}

export const api = {
  get: <T>(url: string) => request<T>('GET', url),
  post: <T>(url: string, body?: unknown) => request<T>('POST', url, body ?? {}),
}

export type Status =
  | 'new'
  | 'verified'
  | 'awaiting_invoice'
  | 'payment_pending'
  | 'awaiting_payment'
  | 'approved'
  | 'provisioning'
  | 'active'
  | 'failed'
  | 'rejected'
  | 'suspended'
  | 'removed'

export interface ClientMe {
  kind: 'client'
  email: string
  company: string
  contact_name: string
  phone: string
  slug: string
  bot_host: string
  status: Status
  admin_url: string | null
  reject_reason: string | null
  setup_link_available: boolean
  tariff: string | null
  invoice_no: string | null
  invoice_amount: number | null
  created_at: string
  llm_enabled?: boolean
  local_install?: boolean
  /** invoice | online | free | null */
  pay_method?: string | null
  /** checkout page of a pending online payment (subscription / upgrade) */
  payment_url?: string | null
  free_until?: string | null
  /** unpaid | trial_ended | null */
  suspend_reason?: string | null
  upgrade_tariff?: string | null
  /** "Попросить помочь с настройкой": when asked / when a manager marked that they got in touch */
  setup_help_requested_at?: string | null
  setup_help_contacted_at?: string | null
  /** the bot panel's owner was created from this account: log in there with the cabinet e-mail and password */
  owner_preset?: boolean
}

export interface SupportContacts {
  /** as typed in NOX_SUPPORT_PHONE; "" = not shown */
  phone: string
  /** digits only; "" = not shown */
  whatsapp: string
  whatsapp_url: string
  email: string
}

export interface Seller {
  name: string
  bin: string
  address: string
  bank: string
  iik: string
  bik: string
  kbe: string
  phone: string
}

export interface PublicConfig {
  instance_domain: string
  online_payments: boolean
  online_methods: string[]
  free_days?: number
  /** qr: the invoice is paid by a QR / deeplink (Kaspi); redirect: provider checkout page */
  online_mode?: 'qr' | 'redirect' | null
  coins_per_dialog?: number
  /** the coin pack of "Пополнить" */
  topup?: { coins: number; price_kzt: number }
  support?: SupportContacts
  /** edition date of /offer, /refund, /privacy; "" = draft */
  legal_date?: string
  /** filled-in seller requisites (empty strings for the missing ones) */
  seller?: Seller
  /** static Kaspi Pay QR link ("" = none): unpaid invoices show it, staff confirms the payment by hand */
  kaspi_static_qr?: string
}

export interface CheckoutOut {
  redirect_url: string | null
  qr_payload: string | null
}

export interface TopupOut {
  kind: 'request' | 'invoice' | 'online'
  invoice_number: string | null
  redirect_url: string | null
}

export interface OnlinePaymentOut {
  id: number
  purpose: string
  amount_kzt: number
  provider: string
  provider_payment_id: string | null
  status: string
  invoice_no: string | null
  created_at: string
  updated_at: string
}

export interface StaffMe {
  kind: 'staff'
  id: number
  email: string
  name: string
}

export interface ApplicationListItem {
  id: number
  company: string
  contact_name: string
  email: string
  phone: string
  slug: string
  status: Status
  coins_limit: number | null
  tariff: string | null
  created_at: string
  updated_at: string
}

export interface JobOut {
  id: number
  kind: string
  status: string
  slug: string
  coins: number | null
  has_custom_key: boolean
  has_gateway_key?: boolean
  error: string | null
  created_at: string
  claimed_at: string | null
  finished_at: string | null
}

export interface AuditOut {
  id: number
  action: string
  staff_email: string | null
  account_id: number | null
  company: string | null
  details: Record<string, unknown>
  ip: string | null
  created_at: string
}

export interface ApplicationDetail extends ApplicationListItem {
  bot_host: string
  admin_url: string | null
  port: number | null
  reject_reason: string | null
  last_error: string | null
  email_verified_at: string | null
  register_ip: string | null
  invoice_no: string | null
  invoice_amount: number | null
  invoice_issued_at: string | null
  local_install?: boolean
  pay_method?: string | null
  invoice_note?: string
  free_until?: string | null
  suspend_reason?: string | null
  upgrade_tariff?: string | null
  period_end?: string | null
  renewal_due?: boolean
  topup_requested_at?: string | null
  setup_help_requested_at?: string | null
  setup_help_contacted_at?: string | null
  online_payments?: OnlinePaymentOut[]
  llm?: LlmBalance | null
  keys?: KeyOut[]
  invoices?: InvoiceListItem[]
  payments: PaymentOut[]
  jobs: JobOut[]
  audit: AuditOut[]
}

export interface StaffConfig {
  tariffs: Tariff[]
  coin_presets: number[]
  instance_domain: string
  custom_key_supported: boolean
  topup?: Tariff | null
  gateway_url?: string
  online_payments?: boolean
}

export const STATUS_LABELS: Record<Status, string> = {
  new: 'Новая',
  verified: 'Подтверждена',
  awaiting_invoice: 'Ждёт счёта',
  payment_pending: 'Оплата онлайн',
  awaiting_payment: 'Ждёт оплаты',
  approved: 'Одобрена',
  provisioning: 'Создаётся',
  active: 'Работает',
  failed: 'Ошибка',
  rejected: 'Отклонена',
  suspended: 'Приостановлена',
  removed: 'Удалена',
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('ru-RU', {
    timeZone: 'Asia/Almaty',
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export interface Tariff {
  code: string
  name: string
  price_kzt: number
  coins: number
  description: string
  /** Free: false when every test place is taken */
  available?: boolean
  /** about how many client dialogs the coins pay for (computed by the api) */
  dialogs?: number | null
}

export const FREE_CODE = 'free'

export interface PaymentOut {
  id: number
  invoice_no: string | null
  tariff: string | null
  amount_kzt: number
  method: string
  reference: string
  note: string
  staff_email: string | null
  created_at: string
}

export interface Invoice {
  number: string
  issued_at: string
  due_days: number
  amount_kzt: number
  tariff: Tariff
  buyer_company: string
  buyer_contact: string
  buyer_email: string
  buyer_phone: string
  bot_host: string
  seller: { name: string; bin: string; address: string; bank: string; iik: string; bik: string; kbe: string; phone: string }
  paid: boolean
  kind?: InvoiceKind
  period_start?: string | null
  period_end?: string | null
  comment?: string
}

export type InvoiceKind = 'subscription' | 'renewal' | 'topup' | 'upgrade'

export interface LlmBalance {
  coins_left: number
  monthly_limit: number
  monthly_coins: number
  topup_coins: number
  spent: number
  period_start: string | null
  period_end: string | null
  status: 'active' | 'suspended' | 'revoked'
  /** about how many client dialogs coins_left is enough for */
  dialogs_left?: number
  /** Free (test): the coins work until the test ends (ISO-8601), not until period_end */
  trial_end?: string | null
}

export interface UsageOut {
  balance: LlmBalance
  by_day: { day: string; coins: number }[]
  by_model: { provider: string; model: string; requests: number; input_tokens: number; output_tokens: number; coins: number }[]
}

export interface InvoiceListItem {
  id: number | null
  number: string
  kind: InvoiceKind
  tariff: string | null
  amount_kzt: number
  coins: number | null
  issued_at: string | null
  period_start: string | null
  paid: boolean
}

export interface KeyOut {
  id: number
  prefix: string
  status: string
  created_at: string
  last_used_at: string | null
  revoked_at: string | null
}

export interface KeyIssued {
  ok: boolean
  prefix: string
  key: string | null
  job_id: number | null
}

export const INVOICE_KIND: Record<InvoiceListItem['kind'], string> = {
  subscription: 'Подключение',
  renewal: 'Продление',
  topup: 'Пакет коинов',
  upgrade: 'Переход на тариф',
}

let publicConfig: Promise<PublicConfig> | null = null

/** /api/public-config, fetched once per page load (a failure is not cached). */
export function loadPublicConfig(): Promise<PublicConfig> {
  if (!publicConfig) {
    publicConfig = api.get<PublicConfig>('/api/public-config').catch((err) => {
      publicConfig = null
      throw err
    })
  }
  return publicConfig
}

export function resetPublicConfig() {
  publicConfig = null
}

/** Full-page navigation (an object so tests can replace it). */
export const browser = {
  go(url: string) {
    window.location.assign(url)
  },
}

/** Go to a provider checkout page (or the mock checkout of the SPA). */
export function goToCheckout(url: string) {
  browser.go(url)
}

export function coins(n: number): string {
  return Math.floor(n).toLocaleString('ru-RU').replace(/ | /g, ' ')
}

/** Coins of one typical dialog when the public config has not loaded (the portal default, NOX_COINS_PER_DIALOG). */
export const DEFAULT_COINS_PER_DIALOG = 5

/**
 * Spent coins by the same rule as the bot's admin panel: under 100 up to two decimals, from 100 whole coins
 * (2.318 -> "2,32", 1234.6 -> "1 235"). The balance itself stays whole coins (coins()).
 */
export function spentCoins(n: number): string {
  const v = Math.abs(n) < 100 ? Math.round(n * 100) / 100 : Math.round(n)
  return v.toLocaleString('ru-RU').replace(/ | /g, ' ')
}

export function formatDay(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('ru-RU', { timeZone: 'Asia/Almaty', day: '2-digit', month: '2-digit', year: 'numeric' })
}

/** Russian plural: plural(5, ['день', 'дня', 'дней']) -> 'дней'. */
export function plural(n: number, [one, few, many]: [string, string, string]): string {
  const a = Math.abs(Math.trunc(n))
  const d = a % 10
  const dd = a % 100
  if (d === 1 && dd !== 11) return one
  if (d >= 2 && d <= 4 && (dd < 12 || dd > 14)) return few
  return many
}

/** "≈ 800 диалогов" (the number comes rounded from the api). */
export function dialogsText(n: number): string {
  return `≈ ${coins(n)} ${plural(n, ['диалог', 'диалога', 'диалогов'])}`
}

/** Whole days left until `iso` (a started day counts as a day), 0 when it has passed. */
export function daysLeft(iso: string | null | undefined, now: number = Date.now()): number {
  if (!iso) return 0
  const ms = new Date(iso).getTime() - now
  return ms <= 0 ? 0 : Math.ceil(ms / 86_400_000)
}

export function money(amount: number): string {
  return `${amount.toLocaleString('ru-RU').replace(/\u00a0|\u202f/g, ' ')} ₸`
}
