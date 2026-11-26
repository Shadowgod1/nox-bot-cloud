// Types for the web <-> core contract. Source of truth: core/iler/api/*.py, core/iler/auth/schemas.py,
// core/iler/settings_models.py, core/iler/bookings/service.py, core/iler/engine/store.py.
// Hand-written until the OpenAPI generator is wired in; keep field names identical to core.

export type Role = 'owner' | 'operator'

/** GET /api/auth/me, POST /api/auth/login, POST /api/setup/owner, POST /api/users. */
export interface User {
  id: number
  email: string
  name: string
  role: Role
}

/** GET /api/users (owner only). */
export interface UserListItem extends User {
  created_at: string
  last_login_at: string | null
}

export interface UserCreate {
  email: string
  password: string
  name: string
  role: Role
}

export interface Ok {
  ok: boolean
}

export interface SetupState {
  needs_setup: boolean
}

/** GET /api/brand (no session needed): deploy-time brand of the admin, from ILER_BRAND. */
export interface BrandInfo {
  brand: string
}

export type WhatsAppState = 'close' | 'connecting' | 'open' | 'degraded'

/** GET /api/whatsapp/status and the `whatsapp.status` SSE event. */
export interface WhatsAppStatus {
  status: WhatsAppState
  phone: string | null
  last_error: string | null
  /** Only on the SSE event sent when the number has been down for a while. */
  alert?: boolean
  down_minutes?: number
}

/** GET /api/dashboard. */
export interface Dashboard {
  whatsapp: { status: WhatsAppState; phone: string | null }
  bot_enabled: boolean
  today: {
    /** Distinct conversations with at least one message today. */
    conversations: number
    client_messages: number
    bot_replies: number
    /** All requests currently awaiting a decision (not only today's). */
    bookings_pending: number
    /** Requests created today. */
    bookings_total: number
  }
  errors_24h: {
    /** Failed LLM attempts in the last 24 h. */
    llm: number
    /** Outbound messages that ended failed/uncertain in the last 24 h. */
    send: number
  }
  /** Result of the last backup attempt (status null = no backup has run yet). */
  backup: { status: 'ok' | 'error' | null; at: string | null; message: string | null }
  health: DashboardHealth
  coins: DashboardCoins
}

export interface DashboardHealth {
  last_bot_reply_at: string | null
  last_client_message_at: string | null
  /** Mean model latency of today's successful requests, ms. */
  avg_reply_ms_today: number | null
  /** Conversations that got status needs_operator today, for any reason. */
  handovers_today: number
  /** Share (0..1) of today's client conversations the bot answered itself (a handover reply does not count). */
  answered_share_today: number | null
}

/** ILER coins: 1 coin = $0.001 of model cost; the monthly limit is set by ILER. */
export interface DashboardCoins {
  /** 0 = no limit configured. */
  limit: number
  spent: number
  remaining: number | null
  percent: number | null
  exhausted: boolean
  avg_per_day: number
  projected_month: number
  /** Date (YYYY-MM-DD) the limit is reached at the current pace; null = lasts the month (or no limit). */
  runs_out_on: string | null
  days_in_month: number
  /** Every day from the 1st to today, zeros included. */
  days: { date: string; coins: number }[]
  by_model: { model: string; coins: number }[]
  /** Models used this month with no price entry: billed at the highest tariff. */
  unpriced_models: string[]
  /** "gateway": limit/remaining/spent come from the LLM gateway balance; days/by_model stay local stats. */
  source?: 'local' | 'gateway'
  /** Gateway: end of the paid period (ISO-8601). */
  period_end?: string | null
  /** Gateway: the balance could not be read just now; the last known value is shown. */
  stale?: boolean
  /** Gateway: when the shown balance was read (ISO-8601). */
  balance_at?: string | null
  /** Gateway: set while the bot hands every conversation to the operator. */
  blocked?: 'insufficient_coins' | 'key_revoked' | null
  /** Where the owner tops up coins; null = no link. */
  topup_url?: string | null
}

export type ConversationStatus = 'active' | 'paused' | 'needs_operator' | 'closed'

export type MessageAuthor = 'client' | 'bot' | 'operator' | 'system'

export interface LastMessage {
  text: string
  author: MessageAuthor
  direction: 'in' | 'out'
  created_at: string
}

/** Item of GET /api/conversations; also returned by takeover / release. */
export interface Conversation {
  id: number
  chat_jid: string
  phone: string
  name: string
  status: ConversationStatus
  paused_until: string | null
  last_message_at: string | null
  unread: number
  last_message: LastMessage | null
}

export interface Message {
  id: number
  conversation_id: number
  direction: 'in' | 'out'
  author: MessageAuthor
  text: string
  media: Record<string, unknown> | unknown[] | null
  created_at: string
}

/**
 * Cursor page (conversations and messages). `next_cursor` is opaque: pass it back as `?cursor=`.
 * Messages come newest first.
 */
export interface Page<T> {
  items: T[]
  next_cursor: string | null
}

export type BookingStatus = 'pending' | 'confirmed' | 'rejected' | 'expired'

/** Free-form dict filled by the LLM tool; these are the keys core knows how to describe. */
export interface BookingPayload {
  date?: string
  time?: string
  duration_hours?: number | string
  seats?: number | string
  zone?: string | null
  name?: string | null
  comment?: string | null
  [key: string]: unknown
}

/** GET /api/bookings returns a plain array (newest first, max 200); confirm / reject return one item. */
export interface Booking {
  id: number
  conversation_id: number
  payload: BookingPayload
  status: BookingStatus
  admin_note: string | null
  timeout_notified: boolean
  created_at: string
  decided_at: string | null
  decided_by: string | null
  /** Client phone and WhatsApp name from the conversation ('' when unknown). */
  phone: string
  name: string
}

export interface DayHours {
  open: string
  close: string
  closed: boolean
}

export const WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const
export type Weekday = (typeof WEEKDAYS)[number]

export type WeeklyHours = { always_open: boolean } & Record<Weekday, DayHours>

export type AfterHoursMode = 'reply' | 'silent' | 'fixed_message'

export interface BotSettings {
  enabled: boolean
  name: string
  /** Template the prompt was last generated from (null = written by hand / never generated). */
  template_id: string | null
  /** Field values of that template's form (key -> text). */
  template_values: Record<string, string>
  /** The business prompt the bot actually uses (max 20000). While empty the bot does not reply. */
  prompt: string
  /** The prompt was edited manually since it was last generated from the template. */
  prompt_edited: boolean
  hours: WeeklyHours
  after_hours_mode: AfterHoursMode
  after_hours_message: string
  greeting: string
  buffer_delay_seconds: number
  handover_pause_minutes: number
  memory_messages: number
  booking_confirm_template: string
  booking_reject_template: string
  booking_request_timeout_min: number
  booking_timeout_message: string
  /** Sent once per conversation when the coins run out and the conversation goes to the operator. */
  no_coins_message: string

  // reply rules
  /** Answer only contacts that did not write to the number before the bot was linked. */
  new_contacts_only: boolean
  /** Numbers the bot never answers (digits). */
  exclude_phones: string[]
  /** Inside working hours: reply as usual or stay silent (staff answers during the day). */
  work_hours_mode: WorkHoursMode
  /** IANA time zone of `hours` (default Asia/Almaty). */
  timezone: string
}

export type WorkHoursMode = 'reply' | 'silent'

/** GET/POST /api/settings/bot/pause */
export interface BotPauseState {
  enabled: boolean
}

export type LlmProvider = 'openai' | 'gemini' | 'anthropic'

/** Claude thinking depth (`output_config.effort`): low = fastest replies. */
export type AnthropicEffort = 'low' | 'medium' | 'high'

/**
 * Secret fields come back masked ("••••abcd") or null.
 * Sending the masked string back means "unchanged"; null or "" means "remove".
 */
export type SecretValue = string | null

export interface LlmSettings {
  provider: LlmProvider
  openai_model: string
  openai_fallback_model: string | null
  gemini_model: string
  anthropic_model: string
  anthropic_effort: AnthropicEffort
  emergency_provider: LlmProvider | null
}

export interface SmtpSettings {
  host: string
  port: number
  user: string
  password: SecretValue
  sender: string
  to: string[]
  starttls: boolean
}

export interface NotifySettings {
  admin_phones: string[]
  admin_group_jid: string | null
  smtp: SmtpSettings | null
  // lead notifications
  /** Write-only: "••••" when set, null when not. */
  telegram_bot_token: SecretValue
  telegram_chat_ids: string[]
  /** Address of this admin panel for links in notifications. */
  admin_url: string
}

/** POST /api/settings/notify/telegram/chats */
export interface TelegramChatsResult {
  ok: boolean
  chats: { id: string; title: string }[]
  error?: string
}

export interface NotifyChannelResult {
  ok: boolean
  sent: number
  error?: string | null
}

/** POST /api/settings/notify/test: channels that are not set up are null. */
export interface NotifyTestResult {
  whatsapp: NotifyChannelResult | null
  telegram: NotifyChannelResult | null
}

export interface SenetSettings {
  enabled: boolean
  orgname: string | null
  app_key: SecretValue
}

/** GET /api/settings/llm/providers: providers that have an ILER (vendor) key, in order anthropic, openai, gemini. */
export interface LlmProvidersResult {
  providers: LlmProvider[]
}

/** POST /api/settings/llm/test (body: optional unsaved LlmSettings; nothing is saved). */
export interface LlmTestResult {
  ok: boolean
  error?: string
}

/** POST /api/integrations/senet/test body: optional unsaved values; missing / masked fall back to stored. */
export interface SenetTestRequest {
  orgname?: string | null
  app_key?: string | null
}

/** `error` is "not_configured" when orgname or key is missing. */
export interface SenetTestResult {
  ok: boolean
  error?: string
  count?: number
}

export type TemplateFieldType = 'text' | 'textarea' | 'phone'

export interface TemplateField {
  key: string
  label: string
  type: TemplateFieldType
  required: boolean
  placeholder: string
  hint: string
}

/** GET /api/templates item. defaults: optional BotSettings presets (e.g. name, greeting). */
export interface TemplateMeta {
  id: string
  name: string
  description: string
  fields: TemplateField[]
  defaults: Partial<BotSettings>
}

/** POST /api/templates/{id}/render body; saves nothing. Unknown keys / values > 4000 chars -> 422. */
export interface TemplateRenderRequest {
  values: Record<string, string>
}

/** missing: required field keys left empty. */
export interface TemplateRenderResult {
  prompt: string
  missing: string[]
}

export interface ChatTurn {
  role: 'user' | 'assistant'
  content: string
}

export interface ToolCallInfo {
  name: string
  args: Record<string, unknown>
  result: unknown
  error: string | null
}

export interface TestChatResponse {
  reply: string
  tool_calls: ToolCallInfo[]
}

/** GET /api/system/version. `current` / `available` are null when the updater is unreachable. */
export interface VersionInfo {
  current: string | null
  available: string | null
  core_version: string
  updater_available: boolean
}

export type UpdateState =
  | 'queued'
  | 'backup'
  | 'pulling'
  | 'restarting'
  | 'healthy'
  | 'rolled_back'
  | 'failed'

/** GET /api/system/update/status[?job_id=]; without job_id the last persisted job is used. */
export interface UpdateStatus {
  job_id: string | null
  /** null when no update was ever started. */
  state: UpdateState | null
  log: string
}

export interface BackupInfo {
  name: string
  size: number
  created_at: string
  sha256: string | null
}

/** GET /api/backups. */
export interface BackupList {
  items: BackupInfo[]
}

/** POST /api/backups/{name}/restore (synchronous: returns once the databases are restored). */
export interface RestoreResult {
  restored: string
  version: string | null
  safety_backup: string
  redis: string
}

export interface ApiErrorBody {
  error: { code: string; message: string }
}

/** Conversation row as published by the engine (core/iler/engine/store.py conv_dict): no last_message. */
export type ConversationRow = Omit<Conversation, 'last_message'> & { created_at?: string }

/**
 * SSE payloads (GET /api/events):
 * - message.new: `{conversation_id, message}` (engine/store.py);
 * - conversation.updated: `{conversation: row}` (engine/store.py publishes every change); the flat
 *   Conversation shape is still accepted for compatibility with older cores;
 * - booking.updated: `{booking}`;
 * - whatsapp.status: WhatsAppStatus.
 */
export type ServerEvent =
  | { type: 'message.new'; data: { conversation_id: number; message: Message & { evo_message_id?: string | null } } }
  | { type: 'conversation.updated'; data: Conversation | { conversation: ConversationRow } }
  | { type: 'booking.updated'; data: { booking: Booking } }
  | { type: 'whatsapp.status'; data: WhatsAppStatus }
  | { type: 'bot.status'; data: BotPauseState }
