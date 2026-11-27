/* In-memory fake of the iler-core API for UI development: `npm run dev:mock` (VITE_MOCK=1).
 * Set VITE_MOCK_SETUP=1 to start in the first-run wizard. Brand (GET /api/brand): open any page with
 * ?brand=nox or ?brand=iler (remembered for the tab), or set VITE_MOCK_BRAND=nox. Never bundled in production builds:
 * client.ts imports it only behind `import.meta.env.VITE_MOCK === '1'`.
 * Response shapes, status codes and error codes mirror core (core/iler/api/*.py). */
import type {
  BackupInfo,
  BotSettings,
  Booking,
  ClientCard,
  Conversation,
  ConversationRow,
  Dashboard,
  LlmProvider,
  LlmSettings,
  Message,
  NotifySettings,
  RestoreResult,
  TemplateField,
  TemplateMeta,
  UpdateStatus,
  User,
  UserListItem,
  VersionInfo,
  WhatsAppStatus,
} from './types'

const MASK = '••••'
const mask = (v: string | null) => (v ? (v.length >= 8 ? MASK + v.slice(-4) : MASK) : null)

const now = Date.now()
const ago = (min: number) => new Date(now - min * 60_000).toISOString()

type ConvState = Omit<Conversation, 'last_message'>

interface State {
  needsSetup: boolean
  /** owner created from the Nox portal, wizard not finished (VITE_MOCK_WIZARD=1) */
  wizardPending: boolean
  loggedIn: boolean
  user: User
  users: UserListItem[]
  wa: WhatsAppStatus
  waPolls: number
  conversations: ConvState[]
  messages: Message[]
  bookings: Booking[]
  bot: BotSettings
  llm: LlmSettings
  /** Providers with an ILER (vendor) key: configured by a technician on the server, never in the UI. */
  vendorProviders: LlmProvider[]
  notify: NotifySettings & { _smtpPass: string | null }
  backups: BackupInfo[]
  update: UpdateStatus
  updateTicks: number
  nextId: number
  /** Client cards by conversation id (core: client_profiles by phone). */
  clients: Record<number, { name: string; notes: string[]; updated_at: string }>
  /** A just-registered install: no conversations yet (payback card shows the empty state). */
  fresh: boolean
}

const day = { open: '10:00', close: '24:00', closed: false }
const VOICE_DEFAULT = 'Пока не могу прослушать голосовое, напишите, пожалуйста, текстом 🙏'
const PHOTO_DEFAULT = 'Спасибо! Передам фото мастеру, он подскажет.'
/** Mock coins spent by the test chat and price photos (shown in the payback card). */
let mockCoinsSpent = 0

const s: State = {
  needsSetup: import.meta.env.VITE_MOCK_SETUP === '1',
  wizardPending: import.meta.env.VITE_MOCK_WIZARD === '1',
  fresh: import.meta.env.VITE_MOCK_SETUP === '1',
  loggedIn: sessionStorage.getItem('iler_mock_session') === '1',
  user: { id: 1, email: 'owner@club.local', name: 'Айгерим', role: 'owner' },
  users: [
    { id: 1, email: 'owner@club.local', name: 'Айгерим', role: 'owner', created_at: ago(9000), last_login_at: ago(5) },
    { id: 2, email: 'reception@club.local', name: 'Ресепшн', role: 'operator', created_at: ago(8000), last_login_at: ago(300) },
  ],
  // VITE_MOCK_WA=close starts disconnected (to see the screen before the QR)
  wa: import.meta.env.VITE_MOCK_WA === 'close' ? { status: 'close', phone: null, last_error: null } : { status: 'open', phone: '77015551234', last_error: null },
  waPolls: 0,
  conversations: [
    { id: 1, chat_jid: '77015550101@s.whatsapp.net', phone: '77015550101', name: 'Данияр', status: 'needs_operator', paused_until: null, last_message_at: ago(3), unread: 2 },
    { id: 2, chat_jid: '77015550102@s.whatsapp.net', phone: '77015550102', name: 'Алина', status: 'active', paused_until: null, last_message_at: ago(12), unread: 0 },
    { id: 3, chat_jid: '77015550103@s.whatsapp.net', phone: '77015550103', name: '', status: 'paused', paused_until: new Date(now + 40 * 60_000).toISOString(), last_message_at: ago(55), unread: 0 },
    { id: 4, chat_jid: '77015550104@s.whatsapp.net', phone: '77015550104', name: 'Тимур', status: 'closed', paused_until: null, last_message_at: ago(60 * 26), unread: 0 },
  ],
  messages: [],
  bookings: [
    { id: 12, conversation_id: 2, payload: { date: '2026-11-12', time: '19:00', duration_hours: 3, seats: 5, zone: 'VIP', name: 'Алина', comment: 'День рождения' }, status: 'pending', admin_note: null, timeout_notified: false, created_at: ago(12), decided_at: null, decided_by: null, phone: '77015550102', name: 'Алина' },
    { id: 11, conversation_id: 3, payload: { date: '2026-11-11', time: '19:00', duration_hours: 2, seats: 2 }, status: 'confirmed', admin_note: 'Места 14–15', timeout_notified: false, created_at: ago(70), decided_at: ago(60), decided_by: 'Айгерим', phone: '77015550103', name: '' },
    { id: 10, conversation_id: 4, payload: { date: '2026-11-10', time: '23:00', duration_hours: 8, seats: 1, zone: 'Bootcamp' }, status: 'rejected', admin_note: 'Ночь занята турниром', timeout_notified: true, created_at: ago(60 * 27), decided_at: ago(60 * 26), decided_by: 'Ресепшн', phone: '77015550104', name: 'Тимур' },
  ],
  bot: {
    enabled: true,
    name: 'Ассистент клуба',
    template_id: 'computer_club',
    template_values: {
      business_name: 'CyberArena',
      address: 'Алматы, ул. Абая 10, 2 этаж',
      phone: '+7 701 555 12 34',
      hours: 'Круглосуточно',
      zones: 'Стандарт — 30 ПК, RTX 4060, 165 Гц\nVIP — 12 ПК, RTX 4080, 240 Гц',
      prices: 'Стандарт — 800 ₸/час\nVIP — 1 200 ₸/час\nНочной пакет 23:00–08:00 — 4 500 ₸',
      rules: '',
      payment: 'Наличные, карта, Kaspi',
      extra: '',
    },
    prompt: '',
    prompt_edited: false,
    hours: { always_open: false, mon: day, tue: day, wed: day, thu: day, fri: { ...day, close: '24:00' }, sat: { open: '00:00', close: '24:00', closed: false }, sun: { open: '00:00', close: '24:00', closed: false } },
    after_hours_mode: 'reply',
    after_hours_message: '',
    greeting: 'Здравствуйте! Чем могу помочь?',
    buffer_delay_seconds: 8,
    handover_pause_minutes: 60,
    memory_messages: 14,
    booking_confirm_template: 'Ваша бронь подтверждена ✅ {details}',
    booking_reject_template: 'К сожалению, не получилось забронировать: {reason}',
    booking_request_timeout_min: 15,
    booking_timeout_message: 'Администратор скоро свяжется с вами по заявке.',
    no_coins_message: 'Спасибо за сообщение! Сейчас подключится менеджер и ответит вам.',
    services: [],
    staff: [],
    voice_mode: 'ask_text',
    voice_message: VOICE_DEFAULT,
    photo_mode: 'handover',
    photo_message: PHOTO_DEFAULT,
    // reply rules
    new_contacts_only: false,
    exclude_phones: [],
    work_hours_mode: 'reply',
    timezone: 'Asia/Almaty',
  },
  llm: { provider: 'openai', openai_model: 'gpt-5.4-mini', openai_fallback_model: null, gemini_model: 'gemini-3.8-flash', anthropic_model: 'claude-sonnet-5-5', anthropic_effort: 'low', emergency_provider: null },
  vendorProviders: ['anthropic', 'openai'],
  notify: { admin_phones: ['77015550000'], admin_group_jid: null, smtp: null, _smtpPass: null, telegram_bot_token: null, telegram_chat_ids: [], admin_url: '' },
  backups: [
    { name: 'iler-backup-20261111-040000.tar.gz', size: 18_734_112, created_at: ago(60 * 8), sha256: 'a1b2c3d4' },
    { name: 'iler-backup-20261110-040000.tar.gz', size: 18_102_877, created_at: ago(60 * 32), sha256: 'c3d4e5f6' },
  ],
  update: { job_id: null, state: null, log: '' },
  updateTicks: 0,
  nextId: 1000,
  clients: {
    2: { name: 'Алина', notes: ['Ходит к мастеру Айгерим', 'Удобно по будням после 18:00', 'Аллергия на латекс', 'Просит обращаться на «вы»'], updated_at: ago(12) },
  },
}

{
  const add = (conversation_id: number, author: Message['author'], text: string, min: number) =>
    s.messages.push({ id: s.nextId++, conversation_id, author, direction: author === 'client' ? 'in' : 'out', text, media: null, created_at: ago(min) })
  add(1, 'client', 'Здравствуйте, у вас есть свободные места на вечер?', 9)
  add(1, 'bot', 'Здравствуйте! Сейчас свободно 6 мест в зоне Стандарт и 2 в VIP.', 8)
  add(1, 'client', 'А если нас 10 человек, можно скидку?', 4)
  add(1, 'client', 'Можно поговорить с администратором?', 3)
  add(2, 'client', 'Хочу забронировать VIP на завтра в 19:00 на 5 человек', 14)
  add(2, 'bot', 'Заявка передана администратору.', 12)
  add(3, 'client', 'Мы будем в 19:00', 60)
  add(3, 'operator', 'Хорошо, ждём вас в 19:00', 55)
  add(4, 'client', 'Спасибо!', 60 * 26)
  // Older conversations so the list has more than one page (page size 30).
  for (let k = 0; k < 34; k++) {
    const id = 100 + k
    const min = 60 * 30 + k * 97
    const phone = `7701556${String(1000 + k)}`
    s.conversations.push({ id, chat_jid: `${phone}@s.whatsapp.net`, phone, name: k % 3 ? `Клиент ${k + 1}` : '', status: 'closed', paused_until: null, last_message_at: ago(min), unread: 0 })
    add(id, 'client', 'Сколько стоит час в VIP?', min + 1)
    add(id, 'bot', 'Час в VIP стоит 1 200 ₸.', min)
  }
  // A long history in conversation 1 so "Показать более ранние" has something to load.
  for (let k = 0; k < 40; k++) {
    s.messages.push({ id: 500 + k, conversation_id: 1, author: k % 2 ? 'bot' : 'client', direction: k % 2 ? 'out' : 'in', text: `Старое сообщение ${k + 1}`, media: null, created_at: ago(60 * 48 + (40 - k) * 5) })
  }
}

/* ---------- output helpers (same shapes as core) ---------- */

function lastMessage(cid: number): Conversation['last_message'] {
  let last: Message | undefined
  for (const m of s.messages) if (m.conversation_id === cid && (!last || m.id > last.id)) last = m
  return last ? { text: last.text, author: last.author, direction: last.direction, created_at: last.created_at } : null
}
const convOut = (c: ConvState): Conversation => ({ ...c, last_message: lastMessage(c.id) })
const convRow = (c: ConvState): ConversationRow => ({ ...c, created_at: ago(60 * 24 * 30) })
const sortKey = (c: ConvState) => (c.last_message_at ? Date.parse(c.last_message_at) * 1000 : 0)

const llmOut = (): LlmSettings => ({ ...s.llm })
const notifyOut = (): NotifySettings => ({
  ...strip(s.notify),
  smtp: s.notify.smtp ? { ...s.notify.smtp, password: mask(s.notify._smtpPass) } : null,
  telegram_bot_token: s.notify.telegram_bot_token ? MASK : null, // write-only, like core
})

function strip<T extends object>(o: T): T {
  const c = { ...o } as Record<string, unknown>
  for (const k of Object.keys(c)) if (k.startsWith('_')) delete c[k]
  return c as T
}

/** Omitted or masked = unchanged; null or "" = remove; anything else = new secret (core settings.py). */
function applySecret(current: string | null, incoming: unknown): string | null {
  if (incoming === undefined) return current
  if (incoming === null || incoming === '') return null
  const v = String(incoming)
  return v.startsWith(MASK) ? current : v
}

function json(body: unknown, status = 200): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
const fail = (status: number, code: string, message: string) => json({ error: { code, message } }, status)

const listeners = new Set<MockEventSource>()
function publish(type: string, data: unknown) {
  for (const es of listeners) es._emit(type, data)
}

function addMessage(conversation_id: number, author: Message['author'], text: string): Message {
  const msg: Message = { id: s.nextId++, conversation_id, direction: author === 'client' ? 'in' : 'out', author, text, media: null, created_at: new Date().toISOString() }
  s.messages.push(msg)
  publish('message.new', { conversation_id, message: { ...msg, evo_message_id: null } })
  return msg
}

type Handler = (m: RegExpMatchArray, body: Record<string, unknown>, url: URL, init?: RequestInit) => Response | Promise<Response>
const routes: [string, RegExp, Handler][] = []
const on = (method: string, pattern: string, h: Handler) =>
  routes.push([method, new RegExp(`^${pattern.replace(/:(\w+)/g, '([^/]+)')}$`), h])

/* ---------- brand ---------- */

const MOCK_BRAND_KEY = 'iler.mock.brand'

/** ?brand= wins and is remembered for the tab (SPA redirects drop the query), then VITE_MOCK_BRAND. */
function mockBrand(url: URL): string {
  const fromQuery = url.searchParams.get('brand') ?? new URLSearchParams(window.location.search).get('brand')
  try {
    if (fromQuery === 'nox' || fromQuery === 'iler') window.sessionStorage.setItem(MOCK_BRAND_KEY, fromQuery)
    const remembered = window.sessionStorage.getItem(MOCK_BRAND_KEY)
    if (remembered) return remembered
  } catch {
    if (fromQuery === 'nox' || fromQuery === 'iler') return fromQuery
  }
  return import.meta.env.VITE_MOCK_BRAND === 'nox' ? 'nox' : 'iler'
}

on('GET', '/api/brand', (_m, _b, url) => json({ brand: mockBrand(url) }))

/* ---------- setup / auth ---------- */

on('GET', '/api/setup/state', () => json({ needs_setup: s.needsSetup, wizard_pending: !s.needsSetup && s.wizardPending }))
// Like core: the owner from the Nox portal closes the wizard (it had no owner step)
on('POST', '/api/setup/finish', () => {
  s.wizardPending = false
  return json({ needs_setup: false, wizard_pending: false })
})
on('POST', '/api/setup/owner', (_m, b, _url, init) => {
  if (!s.needsSetup) return fail(409, 'already_set_up', 'Setup is already complete')
  // Like core: the one-time installer token comes in X-ILER-Setup-Token (open /setup?token=anything in the mock).
  const token = new Headers(init?.headers).get('X-ILER-Setup-Token')
  if (!token) return fail(403, 'setup_token_required', 'Setup token required')
  if (token === 'bad') return fail(403, 'setup_token_invalid', 'Setup token invalid')
  if (String(b.password ?? '').length < 10) return fail(422, 'validation_error', 'body.password: Value error, password must be at least 10 characters')
  s.needsSetup = false
  s.user = { id: 1, email: String(b.email).trim().toLowerCase(), name: String(b.name ?? ''), role: 'owner' }
  s.users = [{ ...s.user, created_at: new Date().toISOString(), last_login_at: null }]
  s.wa = { status: 'close', phone: null, last_error: null }
  s.bot = { ...s.bot, prompt: '', template_id: null, template_values: {}, prompt_edited: false, services: [], staff: [] }
  s.fresh = true
  login() // core sets the session + CSRF cookies here
  return json(s.user)
})
function login() {
  s.loggedIn = true
  sessionStorage.setItem('iler_mock_session', '1')
  document.cookie = 'iler_csrf=mock-csrf-token; path=/'
}
on('POST', '/api/auth/login', (_m, b) => {
  if (!b.email || !b.password) return fail(401, 'invalid_credentials', 'Wrong e-mail or password')
  login()
  return json(s.user)
})
on('POST', '/api/auth/logout', () => {
  s.loggedIn = false
  sessionStorage.removeItem('iler_mock_session')
  return json({ ok: true })
})
on('GET', '/api/auth/me', () => json(s.user))
on('POST', '/api/auth/password', (_m, b) =>
  String(b.new ?? '').length < 10
    ? fail(422, 'validation_error', 'body.new: Value error, password must be at least 10 characters')
    : json({ ok: true }),
)

/* ---------- dashboard ---------- */

on('GET', '/api/dashboard', () => {
  const body: Dashboard = {
    whatsapp: { status: s.wa.status, phone: s.wa.phone },
    bot_enabled: s.bot.enabled,
    today: {
      conversations: 14,
      client_messages: 41,
      bot_replies: 63,
      bookings_pending: s.bookings.filter((b) => b.status === 'pending').length,
      bookings_total: 3,
    },
    errors_24h: { llm: 1, send: 0 },
    backup: { status: 'ok', at: ago(60 * 8), message: null },
    health: {
      last_bot_reply_at: ago(4),
      last_client_message_at: ago(5),
      avg_reply_ms_today: 2300,
      handovers_today: 1,
      answered_share_today: 0.93,
    },
    coins: mockCoins(),
  }
  return json(body)
})

/** Month-to-date coin usage: limit 30 000, a realistic weekday/weekend pattern. */
function mockCoins(): Dashboard['coins'] {
  const today = new Date(now)
  const y = today.getFullYear()
  const m = today.getMonth()
  const daysInMonth = new Date(y, m + 1, 0).getDate()
  const pad = (n: number) => String(n).padStart(2, '0')
  const pattern = [980.2, 1210.4, 1342.7, 1105.9, 640.3, 410.8, 1188.1, 1290.6, 1402.2, 1251.4, 1176.5, 720.0]
  const days = Array.from({ length: today.getDate() }, (_, i) => ({
    date: `${y}-${pad(m + 1)}-${pad(i + 1)}`,
    coins: pattern[i % pattern.length],
  }))
  const limit = 30_000
  const spent = Math.round(days.reduce((a, d) => a + d.coins, 0) * 10) / 10
  const avg = spent / days.length
  const projected = Math.round(avg * daysInMonth * 10) / 10
  let runsOut: string | null = null
  if (projected > limit) {
    const day = Math.min(daysInMonth, Math.ceil(limit / avg))
    runsOut = `${y}-${pad(m + 1)}-${pad(day)}`
  }
  return {
    limit,
    spent,
    remaining: Math.max(0, limit - spent),
    percent: Math.round((spent / limit) * 1000) / 10,
    exhausted: spent >= limit,
    avg_per_day: Math.round(avg * 10) / 10,
    projected_month: projected,
    runs_out_on: runsOut,
    days_in_month: daysInMonth,
    days,
    by_model: [
      { model: 'claude-sonnet-5-5', coins: Math.round(spent * 0.82 * 10) / 10 },
      { model: 'claude-haiku-5-5', coins: Math.round(spent * 0.18 * 10) / 10 },
    ],
    unpriced_models: [],
  }
}

/* ---------- whatsapp ---------- */

on('GET', '/api/whatsapp/status', () => {
  if (s.wa.status === 'connecting' && ++s.waPolls >= 3) {
    s.wa = { status: 'open', phone: '77015551234', last_error: null }
    publish('whatsapp.status', s.wa)
  }
  return json(s.wa)
})
on('POST', '/api/whatsapp/connect', () => {
  if (s.wa.status === 'open') return fail(409, 'already_connected', 'WhatsApp is already connected')
  s.wa = { status: 'connecting', phone: s.wa.phone, last_error: s.wa.last_error }
  s.waPolls = 0
  publish('whatsapp.status', s.wa)
  return json({ qr_base64: QR_SVG })
})
on('POST', '/api/whatsapp/pairing', (_m, b) => {
  const digits = String(b.phone ?? '').replace(/\D/g, '')
  if (digits.length < 10 || digits.length > 15) return fail(422, 'validation_error', 'body.phone: Value error, phone must have 10-15 digits')
  if (s.wa.status !== 'close') return fail(409, 'wrong_state', `Pairing is possible only when disconnected (now: ${s.wa.status})`)
  s.wa = { status: 'connecting', phone: s.wa.phone, last_error: s.wa.last_error }
  s.waPolls = 0
  publish('whatsapp.status', s.wa)
  return json({ code: 'K7Q2M9XD' })
})
on('POST', '/api/whatsapp/logout', () => {
  s.wa = { status: 'close', phone: null, last_error: null }
  publish('whatsapp.status', s.wa)
  return json({ ok: true })
})

/* ---------- conversations ---------- */

const limitOf = (url: URL) => Math.min(100, Math.max(1, Number(url.searchParams.get('limit') ?? 30) || 30))

on('GET', '/api/conversations', (_m, _b, url) => {
  const st = url.searchParams.get('status')
  const q = (url.searchParams.get('q') ?? '').trim().toLowerCase()
  const digits = q.replace(/\D/g, '')
  const cursor = url.searchParams.get('cursor')
  const limit = limitOf(url)
  let rows = s.conversations
    .filter((c) => !st || c.status === st)
    .filter((c) => !q || c.name.toLowerCase().includes(q) || c.phone.includes(q) || (!!digits && c.phone.includes(digits)))
    .sort((a, b) => sortKey(b) - sortKey(a) || b.id - a.id)
  if (cursor) {
    const m = /^(-?\d+)_(\d+)$/.exec(cursor)
    if (!m) return fail(422, 'validation_error', 'cursor: invalid')
    const [key, cid] = [Number(m[1]), Number(m[2])]
    rows = rows.filter((c) => sortKey(c) < key || (sortKey(c) === key && c.id < cid))
  }
  const page = rows.slice(0, limit)
  const last = page[page.length - 1]
  return json({ items: page.map(convOut), next_cursor: rows.length > limit && last ? `${sortKey(last)}_${last.id}` : null })
})
on('GET', '/api/conversations/:id/messages', (m, _b, url) => {
  const id = Number(m[1])
  const c = s.conversations.find((x) => x.id === id)
  if (!c) return fail(404, 'not_found', 'Conversation not found')
  const cursor = url.searchParams.get('cursor')
  const limit = limitOf(url)
  const rows = s.messages
    .filter((x) => x.conversation_id === id && (!cursor || x.id < Number(cursor)))
    .sort((a, b) => b.id - a.id) // newest first, like core
  const page = rows.slice(0, limit)
  if (c.unread) {
    c.unread = 0
    publish('conversation.updated', { conversation: convRow(c) }) // like core: engine/store publishes
  }
  return json({ items: page, next_cursor: rows.length > limit ? String(page[page.length - 1].id) : null })
})
// like core: the admin-generated request_id is the idempotency key, a repeat does not send again
const operatorRequests = new Set<string>()
on('POST', '/api/conversations/:id/send', (m, b) => {
  const id = Number(m[1])
  const text = String(b.text ?? '')
  if (!text.trim()) return fail(422, 'validation_error', 'body.text: Value error, text must not be blank')
  const requestId = String(b.request_id ?? '')
  if (!/^[A-Za-z0-9_-]{8,64}$/.test(requestId)) return fail(422, 'validation_error', 'body.request_id: invalid')
  const c = s.conversations.find((x) => x.id === id)
  if (!c) return fail(404, 'not_found', 'Conversation not found')
  if (operatorRequests.has(`${id}:${requestId}`)) return json({ ok: true })
  operatorRequests.add(`${id}:${requestId}`)
  const msg = addMessage(id, 'operator', text)
  Object.assign(c, { last_message_at: msg.created_at, status: 'paused', paused_until: new Date(Date.now() + s.bot.handover_pause_minutes * 60_000).toISOString() })
  publish('conversation.updated', { conversation: convRow(c) })
  return json({ ok: true })
})
const setStatus = (status: 'paused' | 'active'): Handler => (m) => {
  const c = s.conversations.find((x) => x.id === Number(m[1]))
  if (!c) return fail(404, 'not_found', 'Conversation not found')
  Object.assign(c, { status, paused_until: null })
  publish('conversation.updated', { conversation: convRow(c) }) // like core: engine/store publishes
  return json(convOut(c))
}
// like core api/client_card.py
const clientCard = (id: number): ClientCard => {
  const p = s.clients[id]
  const when = (b: Booking) => [String(b.payload.date ?? '').split('-').reverse().join('.'), String(b.payload.time ?? '')].filter(Boolean).join(' ')
  return {
    name: p?.name ?? '',
    notes: p?.notes ?? [],
    bookings: s.bookings
      .filter((b) => b.conversation_id === id)
      .sort((a, b) => b.id - a.id)
      .slice(0, 5)
      .map((b) => ({ id: b.id, service: String(b.payload.service ?? b.payload.zone ?? ''), when: when(b), status: b.status, created_at: b.created_at })),
    updated_at: p?.updated_at ?? null,
  }
}
on('GET', '/api/conversations/:id/client', (m) => {
  const id = Number(m[1])
  if (!s.conversations.some((c) => c.id === id)) return fail(404, 'not_found', 'Conversation not found')
  return json(clientCard(id))
})
on('POST', '/api/conversations/:id/client/notes/remove', (m, b) => {
  const id = Number(m[1])
  const p = s.clients[id]
  const fact = String(b.fact ?? '')
  if (!p || !p.notes.includes(fact)) return fail(404, 'not_found', 'Note not found')
  p.notes = p.notes.filter((n) => n !== fact)
  p.updated_at = new Date().toISOString()
  return json(clientCard(id))
})
on('DELETE', '/api/conversations/:id/client', (m) => {
  const id = Number(m[1])
  if (!s.conversations.some((c) => c.id === id)) return fail(404, 'not_found', 'Conversation not found')
  delete s.clients[id]
  return json(clientCard(id))
})
on('POST', '/api/conversations/:id/takeover', setStatus('paused'))
on('POST', '/api/conversations/:id/release', setStatus('active'))

/* ---------- bookings ---------- */

on('GET', '/api/bookings', (_m, _b, url) => {
  const st = url.searchParams.get('status')
  return json(s.bookings.filter((b) => !st || b.status === st).sort((a, b) => b.id - a.id))
})
const decide = (status: 'confirmed' | 'rejected'): Handler => (m, b) => {
  const bk = s.bookings.find((x) => x.id === Number(m[1]))
  if (!bk) return fail(404, 'not_found', 'Booking request not found')
  if (bk.status !== 'pending') return fail(409, 'already_decided', `Booking request is already ${bk.status}`)
  const note = typeof b.note === 'string' && b.note.trim() ? b.note.trim() : null
  Object.assign(bk, { status, admin_note: note, decided_at: new Date().toISOString(), decided_by: s.user.name || s.user.email })
  publish('booking.updated', { booking: bk })
  return json(bk)
}
on('POST', '/api/bookings/:id/confirm', decide('confirmed'))
on('POST', '/api/bookings/:id/reject', decide('rejected'))

/* ---------- settings ---------- */

on('GET', '/api/settings/bot', () => json(s.bot))
on('PUT', '/api/settings/bot', (_m, b) => {
  s.bot = { ...s.bot, ...(b as Partial<BotSettings>) }
  return json(s.bot)
})
on('GET', '/api/settings/llm', () => json(llmOut()))
on('GET', '/api/settings/llm/providers', () => json({ providers: s.vendorProviders }))
on('PUT', '/api/settings/llm', (_m, b) => {
  // like core: keys are not part of the settings; anything the client sends for them is ignored
  const { openai_key: _a, gemini_key: _g, anthropic_key: _c, ...rest } = b
  Object.assign(s.llm, rest)
  return json(llmOut())
})
on('POST', '/api/settings/llm/test', async (_m, b) => {
  await sleep(700)
  const provider = ((b.provider as string | undefined) ?? s.llm.provider) as LlmProvider
  if (!s.vendorProviders.includes(provider)) return json({ ok: false, error: `${provider} API key is not configured` })
  return json({ ok: true })
})
on('POST', '/api/settings/llm/models', async (_m, b) => {
  await sleep(400)
  const provider = ((b.provider as string | undefined) ?? s.llm.provider) as LlmProvider
  if (!s.vendorProviders.includes(provider)) return json({ ok: false, models: [], error: `${provider} API key is not configured` })
  if (provider === 'anthropic') return json({ ok: true, models: ['claude-sonnet-5-5', 'claude-haiku-5-5'] })
  return json({
    ok: true,
    models: provider === 'openai' ? ['gpt-5.5', 'gpt-5.4-mini', 'gpt-5.4-nano'] : ['gemini-3.8-flash', 'gemini-3.5-flash-lite'],
  })
})
on('GET', '/api/settings/notify', () => json(notifyOut()))
on('PUT', '/api/settings/notify', (_m, b) => {
  const smtp = b.smtp as (NotifySettings['smtp'] & { password?: unknown }) | null
  s.notify._smtpPass = smtp ? applySecret(s.notify._smtpPass, smtp.password) : null
  s.notify = {
    ...s.notify,
    admin_phones: (b.admin_phones as string[]) ?? [],
    admin_group_jid: (b.admin_group_jid as string) || null,
    smtp: smtp ? { ...smtp, password: null } : null,
    telegram_bot_token: applySecret(s.notify.telegram_bot_token, b.telegram_bot_token),
    telegram_chat_ids: (b.telegram_chat_ids as string[]) ?? [],
    admin_url: String(b.admin_url ?? ''),
  }
  return json(notifyOut())
})

/* reply rules + lead notifications */
on('GET', '/api/settings/bot/pause', () => json({ enabled: s.bot.enabled }))
on('POST', '/api/settings/bot/pause', (_m, b) => {
  s.bot = { ...s.bot, enabled: !b.paused }
  publish('bot.status', { enabled: s.bot.enabled })
  return json({ enabled: s.bot.enabled })
})
on('GET', '/api/settings/bot/known-contacts', () => json({ count: 37 }))
on('POST', '/api/settings/notify/telegram/chats', async (_m, b) => {
  await sleep(500)
  const token = (b.token as string | null) || s.notify.telegram_bot_token
  if (!token) return json({ ok: false, chats: [], error: 'укажите токен бота' })
  if (!/^\d{5,15}:[A-Za-z0-9_-]{20,}$/.test(token)) return json({ ok: false, chats: [], error: 'токен бота выглядит неверно: он вида 123456789:AA…' })
  return json({ ok: true, chats: [{ id: '501234567', title: 'Айгерим' }] })
})
on('POST', '/api/settings/notify/test', async () => {
  await sleep(600)
  const tg = s.notify.telegram_bot_token && s.notify.telegram_chat_ids.length
  return json({
    whatsapp: s.notify.admin_phones.length ? { ok: true, sent: s.notify.admin_phones.length, error: null } : null,
    telegram: tg ? { ok: true, sent: s.notify.telegram_chat_ids.length, error: null } : null,
  })
})
/* ---------- test chat ---------- */

const usage = (coins: number) => {
  mockCoinsSpent += coins
  return { coins, input_tokens: Math.round(coins * 300), output_tokens: Math.round(coins * 40) }
}

on('POST', '/api/test-chat', async (_m, b) => {
  await sleep(700)
  if (!s.bot.prompt.trim()) return fail(409, 'bot_not_configured', 'Промпт бота пуст: настройте бота по шаблону')
  const msgs = (b.messages as { role: string; content: string; media?: string }[]) ?? []
  const lastMsg = msgs[msgs.length - 1]
  if (lastMsg?.media === 'image') {
    return json({ reply: s.bot.photo_message || PHOTO_DEFAULT, tool_calls: [], handed_over: s.bot.photo_mode === 'handover', usage: null })
  }
  if (lastMsg?.media === 'audio' && !lastMsg.content.trim()) {
    const handover = s.bot.voice_mode === 'handover'
    const text = s.bot.voice_message && s.bot.voice_message !== VOICE_DEFAULT ? s.bot.voice_message : handover ? 'Спасибо! Передам ваше голосовое администратору, он скоро ответит.' : VOICE_DEFAULT
    return json({ reply: text, tool_calls: [], handed_over: handover, usage: null })
  }
  const last = lastMsg?.content.toLowerCase() ?? ''
  if (/[әғқңөұүһі]/i.test(last)) {
    return json({ reply: 'Сәлеметсіз бе! Маникюр 6 000 ₸-дан басталады. Қай күнге жазайын?', tool_calls: [], usage: usage(0.4) })
  }
  if (last.includes('айгерим') || last.includes('четверг')) {
    const a = s.bot.staff.find((x) => x.name.toLowerCase().startsWith('айгерим'))
    const reply = a?.thu.works
      ? `Айгерим в четверг работает с ${a.thu.open} до ${a.thu.close}. Записать вас на 18:00? Точное время подтвердит администратор.`
      : a
        ? 'Айгерим в четверг не работает. Могу предложить другой день или другого мастера.'
        : 'Уточню у администратора, работает ли мастер в четверг, и вернусь с ответом.'
    return json({ reply, tool_calls: [], usage: usage(0.6) })
  }
  if (last.includes('брон') || last.includes('запиш')) {
    return json({
      reply: 'Заявку передала администратору, он подтвердит время.',
      tool_calls: [{ name: 'create_booking_request', args: { date: '2026-11-27', time: '18:00', duration_hours: 1.5, seats: 1, zone: 'Маникюр, Айгерим' }, result: 'Заявка #13 создана', error: null }],
      usage: usage(0.9),
    })
  }
  if (last.includes('где') || last.includes('адрес')) {
    return json({ reply: `Мы находимся по адресу: ${s.bot.template_values.address || 'адрес уточнит администратор'}.`, tool_calls: [], usage: usage(0.3) })
  }
  const mani = s.bot.services.find((x) => x.name.toLowerCase().includes('маникюр'))
  if (last.includes('маникюр') || last.includes('стоит')) {
    const price = mani && /\d/.test(mani.price) && !mani.price.includes('…') ? mani.price : null
    return json({ reply: price ? `${mani!.name} — ${price}. Записать вас?` : 'Цену маникюра уточнит администратор — сейчас передам вопрос.', tool_calls: [], usage: usage(0.5) })
  }
  return json({ reply: 'Здравствуйте! Подскажу по услугам и ценам и помогу записаться.', tool_calls: [], usage: usage(0.3) })
})

/* ---------- knowledge ---------- */

on('GET', '/api/knowledge/import-prices', () => json({ photo: true, provider: 'anthropic' }))
on('POST', '/api/knowledge/import-prices', async (_m, b) => {
  await sleep(500)
  if (typeof b.content_base64 === 'string' && b.content_base64) {
    const name = String(b.filename ?? '').toLowerCase()
    if (name.endsWith('.xls')) return fail(422, 'price_import_failed', 'Старый формат Excel (.xls) не поддерживается: сохраните файл как .xlsx или .csv.')
    if (/\.(png|jpe?g|webp)$/.test(name)) {
      await sleep(1200)
      mockCoinsSpent += 12
      return json({
        source: 'image',
        rows: [
          { name: 'Маникюр классический', price: 'от 5 000 ₸', duration_min: 60 },
          { name: 'Покрытие гель-лак', price: '4 000 ₸', duration_min: 45 },
          { name: 'Педикюр', price: 'от 8 000 ₸', duration_min: 90 },
        ],
        warnings: [],
        coins: 12,
      })
    }
    let text = ''
    try {
      text = new TextDecoder().decode(Uint8Array.from(atob(b.content_base64), (c) => c.charCodeAt(0)))
    } catch {
      return fail(422, 'validation_error', 'Файл повреждён: загрузите его снова')
    }
    return json(mockParse(text.split(/\r?\n/).slice(name.endsWith('.csv') ? 1 : 0).join('\n').replace(/[;,]/g, ' — '), name.endsWith('.xlsx') ? 'xlsx' : 'csv'))
  }
  const text = String(b.text ?? '')
  if (!text.trim()) return fail(422, 'validation_error', 'Загрузите файл или вставьте текст прайса')
  return json(mockParse(text, 'text'))
})

/** Rough stand-in for core's parser: "Маникюр — 5000, 60 мин" per line. */
function mockParse(text: string, source: string) {
  const rows: { name: string; price: string; duration_min: number | null }[] = []
  let skipped = 0
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line) continue
    const dm = line.match(/(\d+)\s*мин/)
    const dh = line.match(/(\d+(?:[.,]\d)?)\s*ч/)
    const rest = line.replace(/(\d+)\s*мин\S*/, '').replace(/(\d+(?:[.,]\d)?)\s*ч\S*/, '')
    const pm = rest.match(/(от\s*)?(\d[\d\s]*\d|\d)(?!.*\d)/)
    if (!pm) {
      skipped++
      continue
    }
    const name = rest.slice(0, pm.index).replace(/[\s—–:\-|;,.]+$/, '').replace(/^[\s\d.)•-]+/, '').trim()
    if (!name) continue
    const amount = Number(pm[2].replace(/\s/g, '')).toLocaleString('ru-RU').replace(/ /g, ' ')
    rows.push({ name, price: `${pm[1] ? 'от ' : ''}${amount} ₸`, duration_min: dm ? Number(dm[1]) : dh ? Math.round(Number(dh[1].replace(',', '.')) * 60) : null })
  }
  const warnings = skipped ? [`Пропущено строк без цены: ${skipped} (обычно это заголовки разделов).`] : []
  if (!rows.length) warnings.unshift('Не нашли ни одной услуги с ценой. Проверьте, что в файле есть колонки «Услуга» и «Цена».')
  return { source, rows, warnings, coins: null }
}

/* ---------- overview charts ---------- */

on('GET', '/api/dashboard/activity', (_m, _b, url) => {
  const days = Number(url.searchParams.get('days')) || 14
  const day = (i: number) => {
    const d = new Date(now - (days - 1 - i) * 86_400_000)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  }
  const wave = (i: number) => (s.fresh ? 0 : Math.round(18 + 9 * Math.sin(i / 1.6) + (i % 7 === 5 ? 12 : 0) + i * 0.6))
  const series = Array.from({ length: days }, (_, i) => {
    const client = wave(i)
    return {
      date: day(i),
      conversations: Math.round(client / 3.2),
      client_messages: client,
      bot_replies: Math.round(client * 0.93),
      bookings: Math.round(client / 9),
      handovers: client ? Math.round(client / 30) : 0,
    }
  })
  const sum = (k: 'conversations' | 'client_messages' | 'bot_replies' | 'bookings' | 'handovers') =>
    series.reduce((a, d) => a + d[k], 0)
  const totals = { conversations: sum('conversations'), client_messages: sum('client_messages'), bot_replies: sum('bot_replies'),
    bookings: sum('bookings'), handovers: sum('handovers') }
  const shape = [1, 0, 0, 0, 0, 0, 0, 1, 3, 6, 9, 11, 12, 10, 9, 10, 11, 13, 15, 16, 14, 9, 5, 2]
  const total = totals.client_messages
  const hours = shape.map((v) => Math.round((v / 158) * total))
  const prev = Object.fromEntries(Object.entries(totals).map(([k, v]) => [k, Math.round(v * 0.82)]))
  return json({ days, since: day(0), series, hours, totals, previous: prev, median_reply_seconds: s.fresh ? null : 24 })
})

/* ---------- payback ---------- */

on('GET', '/api/dashboard/roi', (_m, _b, url) => {
  const days = url.searchParams.get('days') === '30' ? 30 : 7
  const since = new Date(now - (days - 1) * 86_400_000)
  since.setHours(0, 0, 0, 0)
  if (s.fresh) {
    return json({ days, since: since.toISOString(), bot_conversations: 0, bookings: 0, after_hours_conversations: 0, handovers: 0, coins: Math.round(mockCoinsSpent * 100) / 100, hours_source: s.bot.hours.always_open ? 'default' : 'settings', has_activity: false })
  }
  const k = days === 30 ? 4.1 : 1
  return json({
    days,
    since: since.toISOString(),
    bot_conversations: Math.round(38 * k),
    bookings: Math.round(11 * k),
    after_hours_conversations: Math.round(9 * k),
    handovers: Math.round(4 * k),
    coins: Math.round((212.4 * k + mockCoinsSpent) * 100) / 100,
    hours_source: s.bot.hours.always_open ? 'default' : 'settings',
    has_activity: true,
  })
})

/* ---------- templates ---------- */

const f = (key: string, label: string, type: TemplateField['type'], required: boolean, placeholder = '', def = '', hint = ''): TemplateField => ({ key, label, type, required, placeholder, hint, default: def })
const svc = (name: string, duration_min: number) => ({ name, price: 'от … ₸', duration_min })
const MORE_SERVICES = f('services', 'Ещё об услугах (текстом)', 'textarea', false, 'Цена окрашивания зависит от длины волос', '', 'Основной прайс — в таблице «Услуги и цены». Сюда — то, что не влезает в таблицу.')
const COMMON_HEAD = [
  f('business_name', 'Название', 'text', true, 'CyberArena'),
  f('address', 'Адрес', 'text', true, 'Алматы, ул. Абая 10'),
  f('phone', 'Телефон для связи', 'phone', false, '+7 701 000 00 00'),
  f('hours', 'Часы работы', 'text', true, 'Ежедневно 10:00–21:00'),
]
const EXTRA = f('extra', 'Дополнительно (акции, парковка, правила)', 'textarea', false)
const TEMPLATES: (TemplateMeta & { prompt: string })[] = [
  {
    id: 'computer_club',
    name: 'Компьютерный клуб',
    description: 'Зоны, железо и цены, заявки на бронь; свободные места подтверждает администратор.',
    fields: [...COMMON_HEAD, f('zones', 'Зоны и железо', 'textarea', true, 'Общий зал — 20 ПК, RTX 4060'), f('prices', 'Тарифы и пакеты', 'textarea', true, 'Общий зал — 800 ₸/час'), f('rules', 'Правила клуба', 'textarea', false), f('payment', 'Способы оплаты', 'text', false), EXTRA],
    defaults: { name: 'Администратор клуба', greeting: 'Привет! Я администратор клуба 🎮' },
    prompt: 'Ты — администратор компьютерного клуба «{{business_name}}» в WhatsApp.\n\nАдрес: {{address}}\nЧасы работы: {{hours}}\n{{#phone}}Телефон: {{phone}}{{/phone}}\n\nЗоны и железо:\n{{zones}}\n\nТарифы:\n{{prices}}\n{{#rules}}\nПравила:\n{{rules}}\n{{/rules}}{{#payment}}\nОплата: {{payment}}\n{{/payment}}{{#extra}}\nДополнительно:\n{{extra}}{{/extra}}\n\nБронь — только через create_booking_request; сложные вопросы — handover_to_operator.',
  },
  {
    id: 'beauty_salon',
    name: 'Салон красоты / барбершоп',
    description: 'Услуги и цены, мастера, заявки на запись на удобное время.',
    fields: [
      ...COMMON_HEAD,
      f('tone', 'Тон общения', 'text', false, 'Тепло и вежливо, на «вы»', 'Тепло и вежливо, на «вы», без канцелярита; смайлики — изредка'),
      f('faq', 'Частые вопросы и ответы', 'textarea', false, '', 'Как записаться? — Напишите услугу, удобный день и время, администратор подтвердит запись.\nСтерилизуете ли инструменты? — Да, инструменты проходят стерилизацию после каждого клиента.\nКак можно оплатить? — Kaspi, картой или наличными.', 'Проверьте ответы и поправьте под свой салон.'),
      f('booking_rules', 'Правила записи', 'textarea', false, '', 'Запись подтверждает администратор.\nОпоздание больше 15 минут — запись может сдвинуться или перенестись.\nОтмена или перенос — не позже чем за 3 часа.'),
      MORE_SERVICES,
      EXTRA,
    ],
    defaults: {
      name: 'Администратор',
      greeting: 'Здравствуйте! Я администратор салона. Подскажу по услугам и ценам и помогу записаться ✂️',
      services: [svc('Маникюр классический', 60), svc('Маникюр с покрытием гель-лак', 90), svc('Наращивание ногтей', 150), svc('Педикюр с покрытием', 90), svc('Женская стрижка', 60), svc('Мужская стрижка', 45), svc('Окрашивание волос', 180), svc('Коррекция и окрашивание бровей', 45)],
    },
    prompt: 'Ты — администратор «{{business_name}}».\n{{#tone}}Тон общения: {{tone}}.{{/tone}}\nАдрес: {{address}}\nЧасы работы: {{hours}}\n{{#faq}}\nЧастые вопросы и ответы:\n{{faq}}{{/faq}}\n{{#booking_rules}}\nПравила записи:\n{{booking_rules}}{{/booking_rules}}',
  },
  {
    id: 'clinic',
    name: 'Клиника / стоматология',
    description: 'Услуги, врачи, заявки на приём. Не даёт медицинских советов.',
    fields: [
      ...COMMON_HEAD,
      f('tone', 'Тон общения', 'text', false, '', 'Вежливо, спокойно и с заботой, на «вы», без смайликов'),
      f('faq', 'Частые вопросы и ответы', 'textarea', false, '', 'Как записаться? — Напишите услугу или врача, удобный день и время, администратор подтвердит запись.\nКак можно оплатить? — Kaspi, картой или наличными.'),
      MORE_SERVICES,
      EXTRA,
    ],
    defaults: {
      name: 'Администратор клиники',
      greeting: 'Здравствуйте! Я администратор клиники. Подскажу по услугам и ценам и помогу записаться на приём.',
      services: [svc('Консультация стоматолога', 30), svc('Профессиональная чистка зубов', 60), svc('Лечение кариеса', 60), svc('Удаление зуба', 45), svc('Отбеливание зубов', 90)],
    },
    prompt: 'Ты — администратор клиники «{{business_name}}». Никогда не ставь диагнозы.\nАдрес: {{address}}\n\nУслуги и цены:\n{{services}}',
  },
  {
    id: 'generic',
    name: 'Универсальный бизнес',
    description: 'Консультации, сбор заявок, передача клиента менеджеру.',
    fields: [f('business_name', 'Название компании', 'text', true), f('activity', 'Чем вы занимаетесь', 'textarea', true), f('services', 'Товары / услуги и цены', 'textarea', true), EXTRA],
    defaults: { name: 'Ассистент' },
    prompt: 'Ты — онлайн-консультант компании «{{business_name}}».\n\n{{activity}}\n\nТовары и услуги:\n{{services}}',
  },
]

/** Same logic-less renderer as core (templates_loader.render). */
function renderPrompt(tpl: string, values: Record<string, string>): string {
  const filled = (k: string) => Boolean((values[k] ?? '').trim())
  let text = tpl
  for (let i = 0; i < 10; i++) {
    const next = text.replace(/\{\{([#^])\s*([a-z][a-z0-9_]*)\s*\}\}([\s\S]*?)\{\{\/\s*\2\s*\}\}/g, (_m, kind: string, key: string, body: string) =>
      filled(key) === (kind === '#') ? body : '',
    )
    if (next === text) break
    text = next
  }
  text = text.replace(/\{\{\s*([a-z][a-z0-9_]*)\s*\}\}/g, (_m, key: string) => (values[key] ?? '').trim())
  return text.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim()
}

s.bot.prompt = renderPrompt(TEMPLATES[0].prompt, s.bot.template_values)

on('GET', '/api/templates', () => json(TEMPLATES.map(({ prompt: _p, ...meta }) => meta) satisfies TemplateMeta[]))
on('POST', '/api/templates/:id/render', (m, b) => {
  const t = TEMPLATES.find((x) => x.id === decodeURIComponent(m[1]))
  if (!t) return fail(404, 'not_found', 'Template not found')
  const values = (b.values ?? {}) as Record<string, unknown>
  const keys = new Set(t.fields.map((x) => x.key))
  for (const [k, v] of Object.entries(values)) {
    if (!keys.has(k)) return fail(422, 'validation_error', `unknown field: ${k}`)
    if (typeof v !== 'string' || v.length > 4000) return fail(422, 'validation_error', `field ${k}: invalid`)
  }
  const vals = values as Record<string, string>
  return json({ prompt: renderPrompt(t.prompt, vals), missing: t.fields.filter((x) => x.required && !(vals[x.key] ?? '').trim()).map((x) => x.key) })
})

/* ---------- users ---------- */

on('GET', '/api/users', () => json(s.users))
on('POST', '/api/users', (_m, b) => {
  if (String(b.password ?? '').length < 10) return fail(422, 'validation_error', 'body.password: Value error, password must be at least 10 characters')
  const email = String(b.email ?? '').trim().toLowerCase()
  if (s.users.some((u) => u.email === email)) return fail(409, 'email_taken', 'A user with this e-mail already exists')
  const u: User = { id: s.nextId++, email, name: String(b.name ?? ''), role: b.role === 'owner' ? 'owner' : 'operator' }
  s.users.push({ ...u, created_at: new Date().toISOString(), last_login_at: null })
  return json(u)
})
on('DELETE', '/api/users/:id', (m) => {
  const id = Number(m[1])
  if (id === s.user.id) return fail(409, 'cannot_delete_self', 'You cannot delete your own account')
  const u = s.users.find((x) => x.id === id)
  if (!u) return fail(404, 'not_found', 'User not found')
  if (u.role === 'owner' && s.users.filter((x) => x.role === 'owner').length <= 1) return fail(409, 'last_owner', 'Cannot delete the last owner')
  s.users = s.users.filter((x) => x.id !== id)
  return json({ ok: true })
})

/* ---------- system ---------- */

on('GET', '/api/system/version', () => {
  const body: VersionInfo = { current: '1.0.0', available: s.update.state === 'healthy' ? null : '1.0.1', core_version: '1.0.0', updater_available: true }
  return json(body)
})
on('POST', '/api/system/update', () => {
  if (s.update.state && !['healthy', 'failed', 'rolled_back'].includes(s.update.state)) return fail(409, 'update_in_progress', 'An update is already running')
  s.update = { job_id: 'job-1', state: 'queued', log: '' }
  s.updateTicks = 0
  return json({ job_id: 'job-1' })
})
on('GET', '/api/system/update/status', () => {
  const steps = ['queued', 'backup', 'pulling', 'restarting', 'healthy'] as const
  if (s.update.state && s.update.state !== 'healthy') {
    s.updateTicks++
    const st = steps[Math.min(steps.length - 1, s.updateTicks)]
    s.update = { ...s.update, state: st, log: `${s.update.log}[${new Date().toLocaleTimeString('ru-RU')}] ${st}\n` }
  }
  return json(s.update)
})

on('GET', '/api/backups', () => json({ items: s.backups }))
on('POST', '/api/backups', async () => {
  await sleep(1200)
  const b = newBackup()
  s.backups.unshift(b)
  return json(b)
})
on('POST', '/api/backups/:name/restore', async (m) => {
  const name = decodeURIComponent(m[1])
  if (!s.backups.some((b) => b.name === name)) return fail(404, 'not_found', 'Backup not found')
  await sleep(1500)
  const safety = newBackup()
  s.backups.unshift(safety)
  const body: RestoreResult = { restored: name, version: '1.0.0', safety_backup: safety.name, redis: 'staged' }
  return json(body)
})

function newBackup(): BackupInfo {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return {
    name: `iler-backup-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}.tar.gz`,
    size: 18_900_000,
    created_at: d.toISOString(),
    sha256: 'e7f8a9b0',
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

const PUBLIC = ['/api/brand', '/api/setup/state', '/api/setup/owner', '/api/auth/login', '/api/auth/logout']

export async function mockFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const url = new URL(input, window.location.origin)
  const method = (init.method ?? 'GET').toUpperCase()
  await sleep(150 + Math.random() * 200)
  // Like core: before setup there are no users, hence no session — everything else is 401.
  if ((s.needsSetup || !s.loggedIn) && !PUBLIC.includes(url.pathname)) return fail(401, 'unauthorized', 'Not signed in')
  const csrfExempt = ['/api/setup/owner', '/api/auth/login', '/api/auth/logout']
  if (method !== 'GET' && !new Headers(init.headers).get('X-ILER-CSRF') && !csrfExempt.includes(url.pathname)) {
    return fail(403, 'csrf_failed', 'CSRF token missing or invalid')
  }
  for (const [m, re, h] of routes) {
    if (m !== method) continue
    const match = url.pathname.match(re)
    if (match) {
      const body = typeof init.body === 'string' && init.body ? (JSON.parse(init.body) as Record<string, unknown>) : {}
      return h(match, body, url, init)
    }
  }
  return fail(404, 'not_found', `Mock: no handler for ${method} ${url.pathname}`)
}

/** Minimal EventSource stand-in: a client writes every ~25 s (payloads shaped like core's engine events). */
export class MockEventSource {
  readonly url: string
  readyState = 1
  onerror: ((e: Event) => void) | null = null
  onopen: ((e: Event) => void) | null = null
  private handlers = new Map<string, Set<(e: MessageEvent) => void>>()
  private timer: ReturnType<typeof setInterval>

  constructor(url: string) {
    this.url = url
    listeners.add(this)
    const lines = ['А сколько стоит ночь?', 'Есть свободные места в VIP?', 'Спасибо, буду через час', 'Можно оплатить картой?']
    let i = 0
    this.timer = setInterval(() => {
      const c = s.conversations[i % 2 === 0 ? 1 : 0]
      const msg = addMessage(c.id, 'client', lines[i++ % lines.length])
      Object.assign(c, { last_message_at: msg.created_at, unread: c.unread + 1 })
      publish('conversation.updated', { conversation: convRow(c) })
    }, 25_000)
  }

  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set())
    this.handlers.get(type)!.add(fn)
  }

  removeEventListener(type: string, fn: (e: MessageEvent) => void) {
    this.handlers.get(type)?.delete(fn)
  }

  _emit(type: string, data: unknown) {
    const ev = new MessageEvent(type, { data: JSON.stringify(data) })
    this.handlers.get(type)?.forEach((fn) => fn(ev))
  }

  close() {
    this.readyState = 2
    clearInterval(this.timer)
    listeners.delete(this)
  }
}

// Stand-in "QR": an SVG data URI so the screen looks right without a real Evolution instance.
const QR_SVG =
  'data:image/svg+xml;base64,' +
  btoa(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 29 29" shape-rendering="crispEdges"><rect width="29" height="29" fill="#fff"/>${Array.from(
      { length: 29 * 29 },
      (_, k) => {
        const x = k % 29
        const y = Math.floor(k / 29)
        const finder = (cx: number, cy: number) => x >= cx && x < cx + 7 && y >= cy && y < cy + 7 && (x === cx || x === cx + 6 || y === cy || y === cy + 6 || (x >= cx + 2 && x <= cx + 4 && y >= cy + 2 && y <= cy + 4))
        const inFinderZone = (x < 8 && y < 8) || (x > 20 && y < 8) || (x < 8 && y > 20)
        const on = inFinderZone ? finder(0, 0) || finder(22, 0) || finder(0, 22) : (x * 7 + y * 13 + x * y) % 5 < 2
        return on ? `<rect x="${x}" y="${y}" width="1" height="1" fill="#111"/>` : ''
      },
    ).join('')}</svg>`,
  )
