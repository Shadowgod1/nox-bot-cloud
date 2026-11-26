import { useEffect, useState, type ReactNode } from 'react'
import { BellRing, CircleAlert, MessageCircle, Search, Send } from 'lucide-react'
import { api } from '../api/client'
import type { NotifySettings, NotifyTestResult, TelegramChatsResult, WhatsAppStatus } from '../api/types'
import { useLoad } from '../hooks/useLoad'
import { PhoneListInput, phoneDigits } from './PhoneListInput'
import { SecretInput, secretPayload, secretState, type SecretState } from './SecretInput'
import { toast } from './toast'
import { Button, ErrorState, Spinner, cx } from './ui'

interface Form {
  phones: string[]
  token: SecretState
  chatIds: string[]
}

const toForm = (n: NotifySettings): Form => ({
  phones: n.admin_phones.map(phoneDigits),
  token: secretState(n.telegram_bot_token),
  chatIds: n.telegram_chat_ids,
})

/** Where new requests go: the admin's WhatsApp and/or the owner's Telegram, with a test button. */
export function LeadNotifications({ onSaved }: { onSaved?: () => void }) {
  const { data, error, loading, reload } = useLoad(() => api.get<NotifySettings>('/api/settings/notify', { silent: true }))
  const wa = useLoad(() => api.get<WhatsAppStatus>('/api/whatsapp/status', { silent: true }))
  const [form, setForm] = useState<Form | null>(null)
  const [chats, setChats] = useState<TelegramChatsResult['chats'] | null>(null)
  const [chatError, setChatError] = useState<string | null>(null)
  const [finding, setFinding] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [test, setTest] = useState<NotifyTestResult | null>(null)

  useEffect(() => {
    if (data && !form) setForm(toForm(data))
  }, [data, form])

  if (loading && !form) return <Spinner />
  if (error && !form) return <ErrorState message={error.message} onRetry={reload} />
  if (!form) return null

  const botPhone = wa.data?.phone ? phoneDigits(wa.data.phone) : null
  const sameAsBot = botPhone ? form.phones.includes(botPhone) : false
  const tokenSet = form.token.mode === 'keep' ? Boolean(form.token.original) : Boolean(form.token.draft.trim())
  const tgReady = tokenSet && form.chatIds.length > 0 && form.token.mode !== 'clear'

  async function save(quiet = false): Promise<boolean> {
    if (!form) return false
    setSaving(true)
    try {
      // the latest stored settings + these fields: SMTP and the admin group stay as they are
      const current = await api.get<NotifySettings>('/api/settings/notify', { silent: true })
      const saved = await api.put<NotifySettings>('/api/settings/notify', {
        ...current,
        admin_phones: form.phones,
        telegram_bot_token: secretPayload(form.token),
        telegram_chat_ids: form.token.mode === 'clear' ? [] : form.chatIds,
        admin_url: current.admin_url || window.location.origin,
      })
      setForm(toForm(saved))
      if (!quiet) toast.success('Сохранено')
      onSaved?.()
      return true
    } catch {
      return false
    } finally {
      setSaving(false)
    }
  }

  async function findChats() {
    if (!form) return
    setFinding(true)
    setChatError(null)
    try {
      const token = form.token.mode === 'replace' ? form.token.draft.trim() : null
      const r = await api.post<TelegramChatsResult>('/api/settings/notify/telegram/chats', { token })
      if (!r.ok) {
        setChatError(r.error ?? 'не получилось')
        setChats(null)
        return
      }
      setChats(r.chats)
      if (r.chats.length === 1 && !form.chatIds.length) setForm({ ...form, chatIds: [r.chats[0].id] })
    } catch {
      /* toast */
    } finally {
      setFinding(false)
    }
  }

  async function sendTest() {
    setTest(null)
    if (!(await save(true))) return
    setTesting(true)
    try {
      setTest(await api.post<NotifyTestResult>('/api/settings/notify/test'))
    } catch {
      /* toast */
    } finally {
      setTesting(false)
    }
  }

  const toggleChat = (id: string) =>
    setForm({ ...form, chatIds: form.chatIds.includes(id) ? form.chatIds.filter((x) => x !== id) : [...form.chatIds, id] })
  const knownChats = [...(chats ?? []), ...form.chatIds.filter((id) => !chats?.some((c) => c.id === id)).map((id) => ({ id, title: `Чат ${id}` }))]

  return (
    <div className="flex flex-col gap-5">
      <p className="text-[13px] leading-relaxed text-ink-2">
        Когда клиент оставляет заявку или просит позвать человека, бот сразу присылает уведомление: имя, телефон, услугу, желаемое время и
        ссылку на переписку в админке.
      </p>

      <Channel icon={<MessageCircle className="size-4" aria-hidden />} title="В WhatsApp администратору">
        <PhoneListInput
          label="Номера администраторов"
          hint="Сообщение придёт с номера, подключённого к боту. Клиенты его не видят. В ответ можно написать «+12» — подтвердить заявку №12, «-12 причина» — отклонить."
          value={form.phones}
          onChange={(phones) => setForm({ ...form, phones })}
          emptyText="Номер не указан — в WhatsApp уведомления не придут."
          max={20}
        />
        {sameAsBot && (
          <p role="alert" className="flex items-start gap-2 text-[13px] text-warn">
            <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
            Это номер самого бота: уведомления придут в чат «с самим собой» на этом же телефоне, их легко пропустить. Лучше укажите личный номер администратора.
          </p>
        )}
      </Channel>

      <Channel icon={<Send className="size-4" aria-hidden />} title="В Telegram">
        <ol className="list-decimal space-y-1 pl-5 text-[13px] text-ink-2 marker:text-ink-3">
          <li>
            В Telegram откройте <span className="font-medium text-ink">@BotFather</span>, отправьте <span className="font-mono">/newbot</span> и
            придумайте имя — он пришлёт токен.
          </li>
          <li>Вставьте токен ниже. Мы храним его в зашифрованном виде и больше нигде не показываем.</li>
          <li>Напишите вашему новому боту любое сообщение (например, «привет») и нажмите «Найти чаты».</li>
        </ol>
        <SecretInput
          label="Токен Telegram-бота"
          value={form.token}
          onChange={(token) => {
            setForm({ ...form, token })
            setChats(null)
          }}
          placeholder="123456789:AA…"
        />
        {form.token.mode !== 'clear' && (
          <div className="flex flex-col gap-2">
            <div>
              <Button onClick={findChats} loading={finding} disabled={!tokenSet} icon={<Search className="size-4" aria-hidden />}>
                Найти чаты
              </Button>
            </div>
            {chatError && <p className="text-[13px] text-danger">Не получилось: {chatError}</p>}
            {chats && chats.length === 0 && (
              <p className="text-[13px] text-ink-2">Пока никто не писал боту. Напишите ему сообщение в Telegram и нажмите ещё раз.</p>
            )}
            {knownChats.length > 0 && (
              <fieldset className="flex flex-col gap-1.5">
                <legend className="mb-1 text-[13px] font-medium text-ink">Куда присылать</legend>
                {knownChats.map((c) => (
                  <label key={c.id} className="flex items-center gap-2 text-sm text-ink">
                    <input type="checkbox" checked={form.chatIds.includes(c.id)} onChange={() => toggleChat(c.id)} className="accent-[var(--color-accent)]" />
                    {c.title}
                  </label>
                ))}
              </fieldset>
            )}
          </div>
        )}
      </Channel>

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="primary" loading={saving && !testing} onClick={() => void save()}>
          Сохранить
        </Button>
        <Button
          onClick={sendTest}
          loading={testing}
          disabled={saving || (!form.phones.length && !tgReady)}
          icon={<BellRing className="size-4" aria-hidden />}
        >
          Отправить тестовое уведомление
        </Button>
      </div>
      {test && <TestLine result={test} />}
    </div>
  )
}

function Channel({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3 rounded-[10px] border border-line p-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-ink">
        <span className="text-accent">{icon}</span>
        {title}
      </h3>
      {children}
    </section>
  )
}

function TestLine({ result }: { result: NotifyTestResult }) {
  const lines: [string, boolean, string][] = []
  if (result.whatsapp) lines.push(['WhatsApp', result.whatsapp.ok, result.whatsapp.ok ? 'отправлено' : (result.whatsapp.error ?? 'не отправлено')])
  if (result.telegram) lines.push(['Telegram', result.telegram.ok, result.telegram.ok ? 'отправлено' : (result.telegram.error ?? 'не отправлено')])
  if (!lines.length) return <p className="text-[13px] text-ink-2">Укажите номер администратора или подключите Telegram.</p>
  return (
    <ul role="status" className="flex flex-col gap-1 text-[13px]">
      {lines.map(([channel, ok, text]) => (
        <li key={channel} className={cx(ok ? 'text-ok' : 'text-danger')}>
          {channel}: {text}
          {ok ? '. Проверьте, пришло ли сообщение.' : ''}
        </li>
      ))}
    </ul>
  )
}
