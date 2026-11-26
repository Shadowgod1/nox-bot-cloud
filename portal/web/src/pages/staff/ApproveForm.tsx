import { useState, type FormEvent } from 'react'
import { api, type StaffConfig } from '../../api'
import { Alert, Button, cx, Field, Input } from '../../components/ui'
import { slugProblem } from '../Register'

interface Props {
  applicationId: number
  defaultSlug: string
  config: StaffConfig
  onDone: () => void
}

const fmt = (n: number) => n.toLocaleString('ru-RU')

/** Approve: monthly coin limit (preset or custom), optional slug change, optional write-only Anthropic key. */
export function ApproveForm({ applicationId, defaultSlug, config, onDone }: Props) {
  const [preset, setPreset] = useState<number | 'custom'>(config.coin_presets[0] ?? 30000)
  const [custom, setCustom] = useState('')
  const [slug, setSlug] = useState(defaultSlug)
  const [key, setKey] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const coins = preset === 'custom' ? (/^\d{1,9}$/.test(custom) ? Number(custom) : null) : preset
  const slugError = slug !== defaultSlug ? slugProblem(slug) : null
  const keyError = key && !/^sk-ant-[A-Za-z0-9._-]{16,240}$/.test(key.trim()) ? 'Ключ должен начинаться с sk-ant-' : null

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (coins === null || slugError || keyError) return
    setBusy(true)
    setError(null)
    try {
      const body: Record<string, unknown> = { coins }
      if (slug !== defaultSlug) body.slug = slug
      if (key.trim()) body.anthropic_key = key.trim()
      await api.post(`/api/staff/applications/${applicationId}/approve`, body)
      setKey('')
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось одобрить заявку.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4" aria-label="Одобрить заявку" noValidate>
      <fieldset>
        <legend className="text-[13px] font-semibold text-ink-2">Лимит коинов в месяц</legend>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {config.coin_presets.map((p) => (
            <label
              key={p}
              className={cx(
                'flex h-9 cursor-pointer items-center rounded-nx border px-3 text-[13.5px] font-semibold',
                preset === p ? 'border-accent bg-accent-soft text-accent-ink' : 'border-line-strong bg-raised text-ink-2',
              )}
            >
              <input type="radio" name="coins" className="sr-only" checked={preset === p} onChange={() => setPreset(p)} />
              {fmt(p)}
            </label>
          ))}
          <label
            className={cx(
              'flex h-9 cursor-pointer items-center rounded-nx border px-3 text-[13.5px] font-semibold',
              preset === 'custom' ? 'border-accent bg-accent-soft text-accent-ink' : 'border-line-strong bg-raised text-ink-2',
            )}
          >
            <input type="radio" name="coins" className="sr-only" checked={preset === 'custom'} onChange={() => setPreset('custom')} />
            Другой
          </label>
        </div>
        {preset === 'custom' && (
          <Field label="Свой лимит" className="mt-3" hint="0 — без лимита" error={custom && coins === null ? 'Целое число' : null}>
            <Input value={custom} onChange={(e) => setCustom(e.target.value.trim())} inputMode="numeric" className="max-w-[200px]" />
          </Field>
        )}
      </fieldset>
      <Field label="Адрес бота" hint={`https://${slug || '…'}.${config.instance_domain}`} error={slugError}>
        <Input value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase().trim())} spellCheck={false} className="max-w-[320px]" />
      </Field>
      {config.custom_key_supported && (
        <Field
          label="Ключ Anthropic клиента (необязательно)"
          hint="Только запись: ключ шифруется для провизионера и не показывается. Пусто — общий ключ Nox."
          error={keyError}
        >
          <Input
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            placeholder="sk-ant-api03-…"
            className="max-w-[420px]"
          />
        </Field>
      )}
      {error && <Alert>{error}</Alert>}
      <div>
        <Button type="submit" variant="primary" loading={busy} disabled={coins === null || !!slugError || !!keyError}>
          Одобрить и создать бота
        </Button>
      </div>
    </form>
  )
}
