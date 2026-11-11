// Types for the web <-> core contract (spec section 7, models in core/iler/settings_models.py).
// Hand-written until the OpenAPI generator is wired in; keep field names identical to core.

export type Role = 'owner' | 'operator'

export interface User {
  id: number
  email: string
  name: string
  role: Role
  created_at?: string
  last_login_at?: string | null
}

export interface SetupState {
  needs_setup: boolean
}

export type WhatsAppState = 'close' | 'connecting' | 'open' | 'degraded'

export interface WhatsAppStatus {
  status: WhatsAppState
  phone: string | null
  last_error: string | null
}

export interface DashboardError {
  at: string
  source: string
  message: string
}

export interface Dashboard {
  whatsapp: WhatsAppStatus
  bot_enabled: boolean
  today: {
    conversations: number
    replies: number
    bookings: number
    needs_operator?: number
  }
  errors: DashboardError[]
}

export type ConversationStatus = 'active' | 'paused' | 'needs_operator' | 'closed'

export interface Conversation {
  id: number
  chat_jid: string
  phone: string
  name: string
  status: ConversationStatus
  paused_until: string | null
  last_message_at: string | null
  unread: number
  last_text?: string | null
}

export type MessageAuthor = 'client' | 'bot' | 'operator' | 'system'

export interface Message {
  id: number
  conversation_id: number
  direction: 'in' | 'out'
  author: MessageAuthor
  text: string
  media?: unknown
  created_at: string
}

export interface Page<T> {
  items: T[]
  next_cursor: number | null
}

export type BookingStatus = 'pending' | 'confirmed' | 'rejected' | 'expired'

export interface BookingPayload {
  date?: string
  time?: string
  duration_hours?: number
  seats?: number
  zone?: string | null
  name?: string | null
  comment?: string | null
}

export interface Booking {
  id: number
  conversation_id: number
  payload: BookingPayload
  status: BookingStatus
  admin_note: string | null
  timeout_notified?: boolean
  created_at: string
  decided_at: string | null
  decided_by: string | null
  phone?: string
  client_name?: string
}

export interface ServiceItem {
  name: string
  price: string
  note: string
}

export interface FaqItem {
  q: string
  a: string
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
  persona: string
  instructions: string
  services: ServiceItem[]
  faq: FaqItem[]
  address: string
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
}

export type LlmProvider = 'openai' | 'gemini'

/**
 * Secret fields come back masked ("••••abcd") or null.
 * Sending the masked string back means "unchanged"; null means "remove".
 */
export type SecretValue = string | null

export interface LlmSettings {
  provider: LlmProvider
  openai_key: SecretValue
  openai_model: string
  openai_fallback_model: string | null
  gemini_key: SecretValue
  gemini_model: string
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
}

export interface SenetSettings {
  enabled: boolean
  orgname: string | null
  app_key: SecretValue
}

export interface CheckResult {
  ok: boolean
  message?: string
}

export interface TemplateMeta {
  id: string
  name: string
  description: string
}

export interface ChatTurn {
  role: 'user' | 'assistant'
  content: string
}

export interface ToolCallInfo {
  name: string
  args: Record<string, unknown>
  result?: unknown
  error?: string | null
}

export interface TestChatResponse {
  reply: string
  tool_calls: ToolCallInfo[]
}

export interface VersionInfo {
  current: string
  available: string | null
}

export type UpdateState =
  | 'idle'
  | 'queued'
  | 'backup'
  | 'pulling'
  | 'restarting'
  | 'healthy'
  | 'rolled_back'
  | 'failed'

export interface UpdateStatus {
  state: UpdateState
  log?: string
  job_id?: string
}

export interface BackupInfo {
  name: string
  size: number
  created_at: string
  sha256?: string
}

export interface ApiErrorBody {
  error: { code: string; message: string }
}

export type ServerEvent =
  | { type: 'message.new'; data: Message | { conversation_id: number; message: Message } }
  | { type: 'conversation.updated'; data: Partial<Conversation> & { id: number } }
  | { type: 'booking.updated'; data: Partial<Booking> & { id: number } }
  | { type: 'whatsapp.status'; data: WhatsAppStatus }
