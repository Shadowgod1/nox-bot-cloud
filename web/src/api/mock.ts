/* In-memory fake of the iler-core API for UI development: `npm run dev:mock` (VITE_MOCK=1).
 * Set VITE_MOCK_SETUP=1 to start in the first-run wizard. Never bundled in production builds:
 * client.ts imports it only behind `import.meta.env.VITE_MOCK === '1'`. */
import type {
  BackupInfo,
  BotSettings,
  Booking,
  Conversation,
  LlmSettings,
  Message,
  NotifySettings,
  SenetSettings,
  TemplateMeta,
  User,
  UpdateStatus,
  WhatsAppStatus,
} from './types'

const MASK = '••••'
const mask = (v: string | null) => (v ? MASK + v.slice(-4) : null)

const now = Date.now()
const ago = (min: number) => new Date(now - min * 60_000).toISOString()

interface State {
  needsSetup: boolean
  loggedIn: boolean
  user: User
  users: User[]
  wa: WhatsAppStatus
  waPolls: number
  conversations: Conversation[]
  messages: Message[]
  bookings: Booking[]
  bot: BotSettings
  llm: LlmSettings & { _openai: string | null; _gemini: string | null }
  notify: NotifySettings & { _smtpPass: string | null }
  senet: SenetSettings & { _key: string | null }
  backups: BackupInfo[]
  update: UpdateStatus
  updateTicks: number
  nextId: number
}

const day = { open: '10:00', close: '24:00', closed: false }

const s: State = {
  needsSetup: import.meta.env.VITE_MOCK_SETUP === '1',
  loggedIn: sessionStorage.getItem('iler_mock_session') === '1',
  user: { id: 1, email: 'owner@club.local', name: 'Айгерим', role: 'owner' },
  users: [
    { id: 1, email: 'owner@club.local', name: 'Айгерим', role: 'owner', created_at: ago(9000), last_login_at: ago(5) },
    { id: 2, email: 'reception@club.local', name: 'Ресепшн', role: 'operator', created_at: ago(8000), last_login_at: ago(300) },
  ],
  wa: { status: 'open', phone: '+7 701 555 12 34', last_error: null },
  waPolls: 0,
  conversations: [
    { id: 1, chat_jid: '77015550101@s.whatsapp.net', phone: '+77015550101', name: 'Данияр', status: 'needs_operator', paused_until: null, last_message_at: ago(3), unread: 2, last_text: 'Можно поговорить с администратором?' },
    { id: 2, chat_jid: '77015550102@s.whatsapp.net', phone: '+77015550102', name: 'Алина', status: 'active', paused_until: null, last_message_at: ago(12), unread: 0, last_text: 'Заявка передана администратору.' },
    { id: 3, chat_jid: '77015550103@s.whatsapp.net', phone: '+77015550103', name: '', status: 'paused', paused_until: new Date(now + 40 * 60_000).toISOString(), last_message_at: ago(55), unread: 0, last_text: 'Хорошо, ждём вас в 19:00' },
    { id: 4, chat_jid: '77015550104@s.whatsapp.net', phone: '+77015550104', name: 'Тимур', status: 'closed', paused_until: null, last_message_at: ago(60 * 26), unread: 0, last_text: 'Спасибо!' },
  ],
  messages: [],
  bookings: [
    { id: 12, conversation_id: 2, payload: { date: '2026-11-12', time: '19:00', duration_hours: 3, seats: 5, zone: 'VIP', name: 'Алина', comment: 'День рождения' }, status: 'pending', admin_note: null, timeout_notified: false, created_at: ago(12), decided_at: null, decided_by: null, phone: '+77015550102', client_name: 'Алина' },
    { id: 11, conversation_id: 3, payload: { date: '2026-11-11', time: '19:00', duration_hours: 2, seats: 2, zone: null, name: null, comment: null }, status: 'confirmed', admin_note: 'Места 14–15', created_at: ago(70), decided_at: ago(60), decided_by: 'Айгерим', phone: '+77015550103' },
    { id: 10, conversation_id: 4, payload: { date: '2026-11-10', time: '23:00', duration_hours: 8, seats: 1, zone: 'Bootcamp' }, status: 'rejected', admin_note: 'Ночь занята турниром', created_at: ago(60 * 27), decided_at: ago(60 * 26), decided_by: 'Ресепшн', phone: '+77015550104', client_name: 'Тимур' },
  ],
  bot: {
    enabled: true,
    name: 'Ассистент клуба',
    persona: 'Дружелюбный администратор компьютерного клуба. Отвечает коротко и по делу.',
    instructions: 'Отвечай на вопросы о ценах, зонах и свободных местах.\nЕсли клиент хочет забронировать — уточни дату, время, длительность и число мест, затем создай заявку.',
    services: [
      { name: 'Стандарт, 1 час', price: '800 ₸', note: 'RTX 4060, 165 Гц' },
      { name: 'VIP, 1 час', price: '1 200 ₸', note: 'RTX 4080, 240 Гц' },
      { name: 'Ночной пакет 23:00–08:00', price: '4 500 ₸', note: '' },
    ],
    faq: [{ q: 'Можно со своей едой?', a: 'Напитки можно, еду — только в лаунж-зоне.' }],
    address: 'Алматы, ул. Абая 10, 2 этаж',
    hours: { always_open: false, mon: day, tue: day, wed: day, thu: day, fri: { ...day, close: '24:00' }, sat: { open: '00:00', close: '24:00', closed: false }, sun: { open: '00:00', close: '24:00', closed: false } },
    after_hours_mode: 'reply',
    after_hours_message: '',
    greeting: 'Здравствуйте! Чем могу помочь?',
    buffer_delay_seconds: 8,
    handover_pause_minutes: 60,
    memory_messages: 20,
    booking_confirm_template: 'Ваша бронь подтверждена ✅ {details}',
    booking_reject_template: 'К сожалению, не получилось забронировать: {reason}',
    booking_request_timeout_min: 15,
    booking_timeout_message: 'Администратор скоро свяжется с вами по заявке.',
  },
  llm: { provider: 'openai', openai_key: null, openai_model: 'gpt-4.1-mini', openai_fallback_model: null, gemini_key: null, gemini_model: 'gemini-2.5-flash', emergency_provider: null, _openai: 'sk-mock-1234abcd', _gemini: null },
  notify: { admin_phones: ['+77015550000'], admin_group_jid: null, smtp: null, _smtpPass: null },
  senet: { enabled: false, orgname: null, app_key: null, _key: null },
  backups: [
    { name: 'iler-backup-20261111-040000.tar.gz', size: 18_734_112, created_at: ago(60 * 8), sha256: 'a1b2…' },
    { name: 'iler-backup-20261110-040000.tar.gz', size: 18_102_877, created_at: ago(60 * 32), sha256: 'c3d4…' },
  ],
  update: { state: 'idle' },
  updateTicks: 0,
  nextId: 1000,
}

{
  const add = (conversation_id: number, author: Message['author'], text: string, min: number) =>
    s.messages.push({ id: s.nextId++, conversation_id, author, direction: author === 'client' ? 'in' : 'out', text, created_at: ago(min) })
  add(1, 'client', 'Здравствуйте, у вас есть свободные места на вечер?', 9)
  add(1, 'bot', 'Здравствуйте! Сейчас свободно 6 мест в зоне Стандарт и 2 в VIP.', 8)
  add(1, 'client', 'А если нас 10 человек, можно скидку?', 4)
  add(1, 'client', 'Можно поговорить с администратором?', 3)
  add(2, 'client', 'Хочу забронировать VIP на завтра в 19:00 на 5 человек', 14)
  add(2, 'bot', 'Заявка передана администратору.', 12)
  add(3, 'client', 'Мы будем в 19:00', 60)
  add(3, 'operator', 'Хорошо, ждём вас в 19:00', 55)
  add(4, 'client', 'Спасибо!', 60 * 26)
}

const llmOut = (): LlmSettings => ({ ...strip(s.llm), openai_key: mask(s.llm._openai), gemini_key: mask(s.llm._gemini) })
const notifyOut = (): NotifySettings => ({ ...strip(s.notify), smtp: s.notify.smtp ? { ...s.notify.smtp, password: mask(s.notify._smtpPass) } : null })
const senetOut = (): SenetSettings => ({ ...strip(s.senet), app_key: mask(s.senet._key) })

function strip<T extends object>(o: T): T {
  const c = { ...o } as Record<string, unknown>
  for (const k of Object.keys(c)) if (k.startsWith('_')) delete c[k]
  return c as T
}

/** Masked value sent back = unchanged; null = remove; anything else = new secret. */
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

type Handler = (m: RegExpMatchArray, body: Record<string, unknown>, url: URL) => Response | Promise<Response>
const routes: [string, RegExp, Handler][] = []
const on = (method: string, pattern: string, h: Handler) =>
  routes.push([method, new RegExp(`^${pattern.replace(/:(\w+)/g, '([^/]+)')}$`), h])

on('GET', '/api/setup/state', () => json({ needs_setup: s.needsSetup }))
on('POST', '/api/setup/owner', (_m, b) => {
  if (!s.needsSetup) return fail(409, 'conflict', 'Владелец уже создан')
  if (String(b.password ?? '').length < 10) return fail(422, 'validation', 'Пароль должен быть не короче 10 символов')
  s.needsSetup = false
  s.user = { id: 1, email: String(b.email), name: String(b.name), role: 'owner' }
  s.users = [s.user]
  s.wa = { status: 'close', phone: null, last_error: null }
  s.llm._openai = null
  login()
  return json(s.user, 201)
})
function login() {
  s.loggedIn = true
  sessionStorage.setItem('iler_mock_session', '1')
  document.cookie = 'iler_csrf=mock-csrf-token; path=/'
}
on('POST', '/api/auth/login', (_m, b) => {
  if (!b.email || !b.password) return fail(401, 'invalid_credentials', 'Неверный e-mail или пароль')
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
  String(b.new ?? '').length < 10 ? fail(422, 'validation', 'Новый пароль должен быть не короче 10 символов') : json({ ok: true }),
)

on('GET', '/api/dashboard', () =>
  json({
    whatsapp: s.wa,
    bot_enabled: s.bot.enabled,
    today: { conversations: 14, replies: 63, bookings: s.bookings.filter((b) => b.status === 'pending').length + 3, needs_operator: s.conversations.filter((c) => c.status === 'needs_operator').length },
    errors: [{ at: ago(95), source: 'llm', message: 'Таймаут ответа модели, использована резервная модель' }],
  }),
)

on('GET', '/api/whatsapp/status', () => {
  if (s.wa.status === 'connecting' && ++s.waPolls >= 3) {
    s.wa = { status: 'open', phone: '+7 701 555 12 34', last_error: null }
    publish('whatsapp.status', s.wa)
  }
  return json(s.wa)
})
on('POST', '/api/whatsapp/connect', () => {
  s.wa = { status: 'connecting', phone: null, last_error: null }
  s.waPolls = 0
  return json({ qr_base64: QR_SVG })
})
on('POST', '/api/whatsapp/pairing', (_m, b) => {
  if (String(b.phone ?? '').replace(/\D/g, '').length < 10) return fail(400, 'bad_phone', 'Укажите номер в международном формате')
  s.wa = { status: 'connecting', phone: null, last_error: null }
  s.waPolls = 0
  return json({ code: 'K7Q2M9XD' })
})
on('POST', '/api/whatsapp/logout', () => {
  s.wa = { status: 'close', phone: null, last_error: null }
  return json({ ok: true })
})

on('GET', '/api/conversations', (_m, _b, url) => {
  const st = url.searchParams.get('status')
  const q = (url.searchParams.get('q') ?? '').toLowerCase()
  const items = s.conversations
    .filter((c) => !st || c.status === st)
    .filter((c) => !q || c.name.toLowerCase().includes(q) || c.phone.includes(q))
    .sort((a, b) => (b.last_message_at ?? '').localeCompare(a.last_message_at ?? ''))
  return json({ items, next_cursor: null })
})
on('GET', '/api/conversations/:id/messages', (m) => {
  const id = Number(m[1])
  const c = s.conversations.find((x) => x.id === id)
  if (c) c.unread = 0
  return json({ items: s.messages.filter((x) => x.conversation_id === id), next_cursor: null })
})
on('POST', '/api/conversations/:id/send', (m, b) => {
  const id = Number(m[1])
  const c = s.conversations.find((x) => x.id === id)
  if (!c) return fail(404, 'not_found', 'Диалог не найден')
  const msg: Message = { id: s.nextId++, conversation_id: id, direction: 'out', author: 'operator', text: String(b.text ?? ''), created_at: new Date().toISOString() }
  s.messages.push(msg)
  Object.assign(c, { last_text: msg.text, last_message_at: msg.created_at, status: 'paused', paused_until: new Date(Date.now() + s.bot.handover_pause_minutes * 60_000).toISOString() })
  publish('conversation.updated', c)
  return json(msg)
})
on('POST', '/api/conversations/:id/takeover', (m) => {
  const c = s.conversations.find((x) => x.id === Number(m[1]))
  if (!c) return fail(404, 'not_found', 'Диалог не найден')
  Object.assign(c, { status: 'paused', paused_until: null })
  return json(c)
})
on('POST', '/api/conversations/:id/release', (m) => {
  const c = s.conversations.find((x) => x.id === Number(m[1]))
  if (!c) return fail(404, 'not_found', 'Диалог не найден')
  Object.assign(c, { status: 'active', paused_until: null })
  return json(c)
})

on('GET', '/api/bookings', (_m, _b, url) => {
  const st = url.searchParams.get('status')
  return json({ items: s.bookings.filter((b) => !st || b.status === st), next_cursor: null })
})
const decide = (status: 'confirmed' | 'rejected'): Handler => (m, b) => {
  const bk = s.bookings.find((x) => x.id === Number(m[1]))
  if (!bk) return fail(404, 'not_found', 'Заявка не найдена')
  if (bk.status !== 'pending') return fail(409, 'conflict', 'Заявка уже обработана')
  Object.assign(bk, { status, admin_note: (b.note as string) || null, decided_at: new Date().toISOString(), decided_by: s.user.name })
  return json(bk)
}
on('POST', '/api/bookings/:id/confirm', decide('confirmed'))
on('POST', '/api/bookings/:id/reject', decide('rejected'))

on('GET', '/api/settings/bot', () => json(s.bot))
on('PUT', '/api/settings/bot', (_m, b) => {
  s.bot = { ...s.bot, ...(b as Partial<BotSettings>) }
  return json(s.bot)
})
on('GET', '/api/settings/llm', () => json(llmOut()))
on('PUT', '/api/settings/llm', (_m, b) => {
  s.llm._openai = applySecret(s.llm._openai, b.openai_key)
  s.llm._gemini = applySecret(s.llm._gemini, b.gemini_key)
  const { openai_key: _a, gemini_key: _g, ...rest } = b
  Object.assign(s.llm, rest)
  return json(llmOut())
})
on('POST', '/api/settings/llm/test', async () => {
  await sleep(700)
  const key = s.llm.provider === 'openai' ? s.llm._openai : s.llm._gemini
  return key ? json({ ok: true, message: `Модель ответила за 640 мс` }) : json({ ok: false, message: 'Ключ не задан' })
})
on('GET', '/api/settings/notify', () => json(notifyOut()))
on('PUT', '/api/settings/notify', (_m, b) => {
  const smtp = b.smtp as (NotifySettings['smtp'] & { password?: unknown }) | null
  s.notify._smtpPass = smtp ? applySecret(s.notify._smtpPass, smtp.password) : null
  s.notify = { ...s.notify, admin_phones: (b.admin_phones as string[]) ?? [], admin_group_jid: (b.admin_group_jid as string) || null, smtp: smtp ? { ...smtp, password: null } : null }
  return json(notifyOut())
})
on('GET', '/api/integrations/senet', () => json(senetOut()))
on('PUT', '/api/integrations/senet', (_m, b) => {
  s.senet._key = applySecret(s.senet._key, b.app_key)
  s.senet.enabled = Boolean(b.enabled)
  s.senet.orgname = (b.orgname as string) || null
  return json(senetOut())
})
on('POST', '/api/integrations/senet/test', async () => {
  await sleep(600)
  return s.senet.orgname && s.senet._key
    ? json({ ok: true, message: 'Подключено: 42 компьютера, свободно 17' })
    : json({ ok: false, message: 'Укажите имя организации и ключ' })
})

on('POST', '/api/test-chat', async (_m, b) => {
  await sleep(900)
  const msgs = (b.messages as { role: string; content: string }[]) ?? []
  const last = msgs[msgs.length - 1]?.content.toLowerCase() ?? ''
  if (last.includes('брон')) {
    return json({
      reply: 'Заявку передала администратору, он подтвердит в течение 15 минут.',
      tool_calls: [{ name: 'create_booking_request', args: { date: '2026-11-12', time: '19:00', duration_hours: 2, seats: 3 }, result: { id: 13, status: 'pending' } }],
    })
  }
  if (last.includes('свобод') || last.includes('мест')) {
    return json({
      reply: 'Сейчас свободно 17 компьютеров: 13 в Стандарте и 4 в VIP.',
      tool_calls: [{ name: 'senet_free_workstations', args: {}, result: { total: 42, free: 17 } }],
    })
  }
  return json({ reply: 'Час в зоне Стандарт стоит 800 ₸, в VIP — 1 200 ₸. Подсказать что-нибудь ещё?', tool_calls: [] })
})

on('GET', '/api/templates', () =>
  json([
    { id: 'computer_club', name: 'Компьютерный клуб', description: 'Цены по зонам, свободные места, заявки на бронь, ночные пакеты.' },
    { id: 'generic', name: 'Универсальный', description: 'Вежливый ассистент: ответы на вопросы, приём заявок, передача оператору.' },
  ] satisfies TemplateMeta[]),
)
on('POST', '/api/templates/:id/apply', () => json({ ok: true }))

on('GET', '/api/users', () => json(s.users))
on('POST', '/api/users', (_m, b) => {
  if (String(b.password ?? '').length < 10) return fail(422, 'validation', 'Пароль должен быть не короче 10 символов')
  const u: User = { id: s.nextId++, email: String(b.email), name: String(b.name ?? ''), role: b.role === 'owner' ? 'owner' : 'operator', created_at: new Date().toISOString(), last_login_at: null }
  s.users.push(u)
  return json(u, 201)
})
on('DELETE', '/api/users/:id', (m) => {
  const id = Number(m[1])
  if (id === s.user.id) return fail(409, 'conflict', 'Нельзя удалить самого себя')
  s.users = s.users.filter((u) => u.id !== id)
  return json(undefined, 204)
})

on('GET', '/api/system/version', () => json({ current: '1.0.0', available: s.update.state === 'healthy' ? null : '1.0.1' }))
on('POST', '/api/system/update', () => {
  s.update = { state: 'queued', job_id: 'job-1', log: '' }
  s.updateTicks = 0
  return json({ job_id: 'job-1' })
})
on('GET', '/api/system/update/status', () => {
  const steps: UpdateStatus['state'][] = ['queued', 'backup', 'pulling', 'restarting', 'healthy']
  if (s.update.state !== 'idle' && s.update.state !== 'healthy') {
    s.updateTicks++
    const st = steps[Math.min(steps.length - 1, s.updateTicks)]
    s.update = { ...s.update, state: st, log: `${s.update.log ?? ''}[${new Date().toLocaleTimeString('ru-RU')}] ${st}\n` }
  }
  return json(s.update)
})

on('GET', '/api/backups', () => json(s.backups))
on('POST', '/api/backups', async () => {
  await sleep(1200)
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  const b: BackupInfo = { name: `iler-backup-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}.tar.gz`, size: 18_900_000, created_at: d.toISOString() }
  s.backups.unshift(b)
  return json(b, 201)
})
on('POST', '/api/backups/:name/restore', async () => {
  await sleep(1500)
  return json({ ok: true })
})

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

const PUBLIC = ['/api/setup/state', '/api/setup/owner', '/api/auth/login']

export async function mockFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const url = new URL(input, window.location.origin)
  const method = (init.method ?? 'GET').toUpperCase()
  await sleep(150 + Math.random() * 200)
  if (s.needsSetup && !url.pathname.startsWith('/api/setup')) return fail(409, 'setup_required', 'Требуется первичная настройка')
  if (!s.loggedIn && !PUBLIC.includes(url.pathname)) return fail(401, 'unauthorized', 'Требуется вход')
  if (method !== 'GET' && !new Headers(init.headers).get('X-ILER-CSRF') && !PUBLIC.includes(url.pathname)) {
    return fail(403, 'csrf', 'Сессия устарела, обновите страницу')
  }
  for (const [m, re, h] of routes) {
    if (m !== method) continue
    const match = url.pathname.match(re)
    if (match) {
      const body = typeof init.body === 'string' && init.body ? (JSON.parse(init.body) as Record<string, unknown>) : {}
      return h(match, body, url)
    }
  }
  return fail(404, 'not_found', `Mock: нет обработчика ${method} ${url.pathname}`)
}

/** Minimal EventSource stand-in: emits a client message every ~25 s. */
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
      const msg: Message = { id: s.nextId++, conversation_id: c.id, direction: 'in', author: 'client', text: lines[i++ % lines.length], created_at: new Date().toISOString() }
      s.messages.push(msg)
      Object.assign(c, { last_text: msg.text, last_message_at: msg.created_at, unread: c.unread + 1 })
      this._emit('message.new', msg)
      this._emit('conversation.updated', c)
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
