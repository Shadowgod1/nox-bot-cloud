import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { NotifySettings, SmtpSettings } from '../../api/types'
import { useLoad } from '../../hooks/useLoad'
import { PageBody } from '../../components/Layout'
import { SecretInput, secretPayload, secretState, type SecretState } from '../../components/SecretInput'
import { Button, ErrorState, Field, Input, PageHeader, Panel, Spinner, Switch } from '../../components/ui'
import { toast } from '../../components/toast'
import { LeadNotifications } from '../../components/LeadNotifications'
import { ru } from '../../i18n/ru'

export function Integrations() {
  return (
    <PageBody>
      <PageHeader title="Интеграции" description="Куда присылать уведомления администратору." />
      {/* 2xl+: two columns: extra channels on the left, lead notifications on the right */}
      <div className="grid items-start gap-5 2xl:grid-cols-2">
        <Panel
          id="leads"
          title="Уведомления о заявках"
          description="Куда бот присылает новые заявки и просьбы позвать человека."
          className="min-w-0 2xl:col-start-2 2xl:row-start-1"
        >
          <LeadNotifications />
        </Panel>
        <div className="min-w-0 2xl:col-start-1 2xl:row-start-1">
          <NotifyPanel />
        </div>
      </div>
    </PageBody>
  )
}

/* ---------- Notifications ---------- */

interface NotifyForm {
  group: string
  smtpOn: boolean
  smtp: Omit<SmtpSettings, 'password' | 'to'> & { to: string }
  smtpPassword: SecretState
}

const emptySmtp = { host: '', port: 587, user: '', sender: '', to: '', starttls: true }

function toNotifyForm(n: NotifySettings): NotifyForm {
  return {
    group: n.admin_group_jid ?? '',
    smtpOn: Boolean(n.smtp),
    smtp: n.smtp ? { host: n.smtp.host, port: n.smtp.port, user: n.smtp.user, sender: n.smtp.sender, to: n.smtp.to.join(', '), starttls: n.smtp.starttls } : emptySmtp,
    smtpPassword: secretState(n.smtp?.password ?? null),
  }
}

const splitList = (s: string) =>
  s
    .split(/[\n,;]+/)
    .map((x) => x.trim())
    .filter(Boolean)

function NotifyPanel() {
  const { data, error, loading, reload } = useLoad(() => api.get<NotifySettings>('/api/settings/notify', { silent: true }))
  const [form, setForm] = useState<NotifyForm | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (data) setForm(toNotifyForm(data))
  }, [data])

  const title = 'Дополнительные каналы'
  const description = 'Группа администраторов в WhatsApp и резервные письма на e-mail.'
  if (loading && !form) return <Panel title={title} description={description}><Spinner /></Panel>
  if (error && !form) return <Panel title={title} description={description}><ErrorState message={error.message} onRetry={reload} /></Panel>
  if (!form) return null

  const smtpInvalid = form.smtpOn && !form.smtp.host.trim()

  async function save() {
    if (!form || smtpInvalid) return
    setSaving(true)
    try {
      // the latest stored settings: the lead-notification fields (numbers, Telegram) stay as they are
      const current = await api.get<NotifySettings>('/api/settings/notify', { silent: true })
      const payload: NotifySettings = {
        ...current,
        admin_group_jid: form.group.trim() || null,
        smtp: form.smtpOn
          ? {
              host: form.smtp.host.trim(),
              port: Number(form.smtp.port) || 587,
              user: form.smtp.user.trim(),
              sender: form.smtp.sender.trim(),
              to: splitList(form.smtp.to),
              starttls: form.smtp.starttls,
              password: secretPayload(form.smtpPassword),
            }
          : null,
      }
      const saved = await api.put<NotifySettings>('/api/settings/notify', payload)
      setForm(toNotifyForm(saved ?? payload))
      toast.success(ru.common.saved)
    } catch {
      /* toast */
    } finally {
      setSaving(false)
    }
  }

  const setSmtp = (patch: Partial<NotifyForm['smtp']>) => setForm({ ...form, smtp: { ...form.smtp, ...patch } })

  return (
    <Panel title={title} description={description}>
      <form
        className="grid gap-5"
        onSubmit={(e) => {
          e.preventDefault()
          void save()
        }}
      >
        <Field label="Группа администраторов (JID)" hint="Необязательно. Идентификатор вида 1203630…@g.us — уведомления пойдут в группу." className="md:max-w-md">
          <Input value={form.group} onChange={(e) => setForm({ ...form, group: e.target.value.trim() })} spellCheck={false} placeholder="120363000000000000@g.us" />
        </Field>

        <div className="rounded-[9px] border border-line p-4">
          <Switch
            checked={form.smtpOn}
            onChange={(v) => setForm({ ...form, smtpOn: v })}
            label="Письма по e-mail (SMTP)"
            description="Резервный канал: например, если WhatsApp отключился дольше чем на 5 минут."
          />
          {form.smtpOn && (
            <div className="mt-5 grid gap-4 md:grid-cols-[minmax(0,1fr)_120px]">
              <Field label="SMTP-сервер" error={smtpInvalid ? 'Укажите сервер' : null}>
                <Input value={form.smtp.host} onChange={(e) => setSmtp({ host: e.target.value })} placeholder="smtp.example.com" spellCheck={false} />
              </Field>
              <Field label="Порт">
                <Input type="number" min={1} max={65535} value={form.smtp.port} onChange={(e) => setSmtp({ port: Number(e.target.value) })} className="tabular" />
              </Field>
              <Field label="Логин">
                <Input value={form.smtp.user} onChange={(e) => setSmtp({ user: e.target.value })} autoComplete="off" spellCheck={false} />
              </Field>
              <div className="md:col-span-1">
                <SecretInput label="Пароль SMTP" value={form.smtpPassword} onChange={(v) => setForm({ ...form, smtpPassword: v })} />
              </div>
              <Field label="Отправитель" className="md:col-span-2" hint="Адрес в поле «От кого».">
                <Input type="email" value={form.smtp.sender} onChange={(e) => setSmtp({ sender: e.target.value })} placeholder="bot@club.kz" />
              </Field>
              <Field label="Получатели" className="md:col-span-2" hint="Через запятую.">
                <Input value={form.smtp.to} onChange={(e) => setSmtp({ to: e.target.value })} placeholder="owner@club.kz, admin@club.kz" />
              </Field>
              <label className="flex items-center gap-2 text-sm text-ink md:col-span-2">
                <input type="checkbox" checked={form.smtp.starttls} onChange={(e) => setSmtp({ starttls: e.target.checked })} className="accent-[var(--color-accent)]" />
                Шифрование STARTTLS
              </label>
            </div>
          )}
        </div>

        <div>
          <Button type="submit" variant="primary" loading={saving} disabled={smtpInvalid}>
            {ru.common.save}
          </Button>
        </div>
      </form>
    </Panel>
  )
}
