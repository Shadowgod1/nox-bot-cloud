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
  payments: PaymentOut[]
  jobs: JobOut[]
  audit: AuditOut[]
}

export interface StaffConfig {
  tariffs: Tariff[]
  coin_presets: number[]
  instance_domain: string
  custom_key_supported: boolean
}

export const STATUS_LABELS: Record<Status, string> = {
  new: 'Новая',
  verified: 'Подтверждена',
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
}

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
}

export function money(amount: number): string {
  return `${amount.toLocaleString('ru-RU').replace(/\u00a0|\u202f/g, ' ')} ₸`
}
