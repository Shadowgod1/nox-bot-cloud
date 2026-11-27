import { useCallback, useEffect, useRef, useState } from 'react'
import { CheckCircle2, FlaskConical, Power, QrCode, RefreshCw, ShieldCheck, Smartphone, UserCheck } from 'lucide-react'
import { api } from '../api/client'
import type { WhatsAppStatus } from '../api/types'
import { Button, Field, Input, Segmented, Spinner, cx } from './ui'
import { ru } from '../i18n/ru'

export const WA_POLL_MS = 4000

type Method = 'qr' | 'code'

/**
 * Connect the business WhatsApp number: QR code or 8-character pairing code.
 * Before the first QR the owner sees what happens to the number (NumberSafety), with one button.
 * Polls /api/whatsapp/status every 4 s until the session is `open`, then stops.
 */
export function WhatsAppConnect({ onConnected, intro = true }: { onConnected?: (s: WhatsAppStatus) => void; intro?: boolean }) {
  const [status, setStatus] = useState<WhatsAppStatus | null>(null)
  const [showIntro, setShowIntro] = useState(intro)
  const [method, setMethod] = useState<Method>('qr')
  const [qr, setQr] = useState<string | null>(null)
  const [code, setCode] = useState<string | null>(null)
  const [phone, setPhone] = useState('')
  const [busy, setBusy] = useState(false)
  const onConnectedRef = useRef(onConnected)
  onConnectedRef.current = onConnected
  const connected = status?.status === 'open'

  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const tick = async () => {
      let next: WhatsAppStatus | null = null
      try {
        next = await api.get<WhatsAppStatus>('/api/whatsapp/status', { silent: true })
      } catch {
        /* keep polling through transient errors */
      }
      if (stopped) return
      if (next) setStatus(next)
      if (next?.status === 'open') {
        onConnectedRef.current?.(next)
        return
      }
      timer = setTimeout(tick, WA_POLL_MS)
    }
    void tick()
    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
    }
  }, [])

  const requestQr = useCallback(async () => {
    setBusy(true)
    setCode(null)
    try {
      const r = await api.post<{ qr_base64?: string | null }>('/api/whatsapp/connect')
      const raw = r?.qr_base64 ?? null
      setQr(raw ? (raw.startsWith('data:') ? raw : `data:image/png;base64,${raw}`) : null)
    } catch {
      /* toast shown by client */
    } finally {
      setBusy(false)
    }
  }, [])

  const requestCode = useCallback(async () => {
    const digits = phone.replace(/\D/g, '')
    setBusy(true)
    setQr(null)
    try {
      const r = await api.post<{ code: string }>('/api/whatsapp/pairing', { phone: digits })
      setCode(r.code)
    } catch {
      /* toast shown by client */
    } finally {
      setBusy(false)
    }
  }, [phone])

  if (!status) return <Spinner label="Проверяем подключение WhatsApp…" />

  if (connected) {
    return (
      <div role="status" className="flex items-center gap-3 rounded-[9px] border border-ok/25 bg-ok-soft px-4 py-3.5">
        <CheckCircle2 className="size-5 shrink-0 text-ok" aria-hidden />
        <div>
          <p className="text-sm font-medium text-ink">WhatsApp подключён</p>
          {status.phone && <p className="tabular text-[13px] text-ink-2">Номер {status.phone}</p>}
        </div>
      </div>
    )
  }

  if (showIntro) {
    return (
      <NumberSafety
        busy={busy}
        onQr={() => {
          setShowIntro(false)
          setMethod('qr')
          void requestQr()
        }}
        onCode={() => {
          setShowIntro(false)
          setMethod('code')
        }}
      />
    )
  }

  return (
    <div className="@container flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Segmented<Method>
          label="Способ подключения"
          value={method}
          onChange={setMethod}
          options={[
            { value: 'qr', label: 'QR-код' },
            { value: 'code', label: 'Код по номеру' },
          ]}
        />
        <p className="flex items-center gap-2 text-[13px] text-ink-2" aria-live="polite">
          <span
            aria-hidden
            className={cx('size-2 rounded-full', status.status === 'connecting' ? 'animate-pulse bg-warn' : 'bg-line-strong')}
          />
          {ru.waStatus[status.status]}
        </p>
      </div>

      {/* status.last_error is technical (Evolution / connection internals): support sees it in «Система», not the client */}

      {method === 'qr' ? (
        <div className="grid gap-5 @md:grid-cols-[220px_1fr] @md:items-start">
          <div className="grid aspect-square w-[220px] place-items-center rounded-[10px] border border-line bg-white p-3">
            {qr ? (
              <img src={qr} alt="QR-код для подключения WhatsApp" className="size-full object-contain [image-rendering:pixelated]" />
            ) : (
              <QrCode className="size-12 text-line-strong" aria-hidden />
            )}
          </div>
          <div className="flex flex-col gap-3 text-sm text-ink-2">
            <ol className="list-decimal space-y-1.5 pl-5 marker:text-ink-3">
              <li>Откройте WhatsApp на рабочем телефоне.</li>
              <li>
                Меню <span className="text-ink">⋮</span> или «Настройки» → «Связанные устройства» → «Привязка устройства».
              </li>
              <li>Наведите камеру телефона на этот QR-код.</li>
            </ol>
            <p className="text-[13px]">Код обновляется примерно раз в минуту. Если он истёк — получите новый.</p>
            <div>
              <Button
                variant={qr ? 'secondary' : 'primary'}
                loading={busy}
                onClick={requestQr}
                icon={qr ? <RefreshCw className="size-4" aria-hidden /> : <QrCode className="size-4" aria-hidden />}
              >
                {qr ? 'Обновить QR-код' : 'Показать QR-код'}
              </Button>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={(e) => {
              e.preventDefault()
              void requestCode()
            }}
          >
            <Field label="Рабочий номер WhatsApp" hint="В международном формате, например +7 701 123 45 67" className="w-full max-w-xs">
              <Input inputMode="tel" autoComplete="tel" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+7 7__ ___ __ __" />
            </Field>
            <Button type="submit" variant="primary" loading={busy} disabled={phone.replace(/\D/g, '').length < 10} icon={<Smartphone className="size-4" aria-hidden />} className="mb-[26px]">
              Получить код
            </Button>
          </form>
          {code && (
            <div className="rounded-[10px] border border-line bg-sunken px-5 py-4">
              <p className="text-[13px] text-ink-2">Код привязки</p>
              <p className="tabular mt-1 font-mono text-[28px] font-semibold tracking-[0.18em] text-ink" aria-label={`Код привязки ${code.split('').join(' ')}`}>
                {code.slice(0, 4)}-{code.slice(4)}
              </p>
              <p className="mt-2 text-[13px] text-ink-2">
                На телефоне: «Связанные устройства» → «Привязка устройства» → «Привязать по номеру телефона» и введите код.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

const SAFETY: { icon: typeof Smartphone; title: string; text: string }[] = [
  {
    icon: Smartphone,
    title: 'WhatsApp на телефоне продолжит работать',
    text:
      'Бот подключается как связанное устройство — так же, как WhatsApp Web на компьютере. Вы и администратор переписываетесь с телефона как раньше и видите все ответы бота.',
  },
  {
    icon: ShieldCheck,
    title: 'Бот пишет только тем, кто написал сам',
    text:
      'Он не делает рассылок и никому не пишет первым. Номера чаще всего блокируют за массовые сообщения незнакомым людям и жалобы на них — бот так не работает. Честно: полной гарантии нет — WhatsApp официально не поддерживает ботов через связанные устройства и сам решает, кого ограничить.',
  },
  {
    icon: UserCheck,
    title: 'Вы решаете, кому он отвечает',
    text:
      'Можно отвечать только новым контактам, не трогать старых клиентов и друзей, добавить номера-исключения. Если вы ответили клиенту сами, бот в этом чате замолкает.',
  },
  {
    icon: Power,
    title: 'Выключить — одной кнопкой',
    text:
      'Переключатель «Бот на паузе» вверху админки. Отвязать совсем: WhatsApp на телефоне → «Связанные устройства» → выйти. Если телефон не выходит в интернет дольше 14 дней, WhatsApp отвяжет бота сам — админка об этом предупредит.',
  },
  {
    icon: FlaskConical,
    title: 'Сначала проверьте ответы',
    text: 'Пока бот не настроен, он никому не отвечает. Проверьте его в «Тестовом чате» — там сообщения не уходят клиентам.',
  },
]

/** The screen before the first QR: honest answers to "what happens to my number". */
export function NumberSafety({ onQr, onCode, busy }: { onQr: () => void; onCode: () => void; busy?: boolean }) {
  return (
    <section aria-labelledby="wa-safety-title" className="@container flex flex-col gap-4">
      <h2 id="wa-safety-title" className="text-[15px] font-semibold text-ink">
        Что будет с вашим номером
      </h2>
      <ul className="grid gap-3 @lg:grid-cols-2">
        {SAFETY.map(({ icon: Icon, title, text }, i) => (
          <li key={title} className={cx('flex gap-3 rounded-[10px] border border-line bg-surface p-3.5', i === SAFETY.length - 1 && '@lg:col-span-2')}>
            <span className="grid size-8 shrink-0 place-items-center rounded-full bg-accent-soft text-accent" aria-hidden>
              <Icon className="size-4" />
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-medium text-ink">{title}</span>
              <span className="mt-0.5 block text-[13px] leading-relaxed text-ink-2">{text}</span>
            </span>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="primary" loading={busy} onClick={onQr} icon={<QrCode className="size-4" aria-hidden />}>
          Показать QR-код
        </Button>
        <button type="button" onClick={onCode} className="text-[13px] text-ink-2 underline hover:text-ink">
          Нет камеры? Подключить по коду
        </button>
      </div>
    </section>
  )
}
