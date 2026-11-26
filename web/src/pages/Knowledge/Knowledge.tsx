import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { FlaskConical } from 'lucide-react'
import { api } from '../../api/client'
import type { BotSettings } from '../../api/types'
import { useLoad } from '../../hooks/useLoad'
import { PageBody } from '../../components/Layout'
import { ServicesEditor, cleanServices } from '../../components/ServicesEditor'
import { StaffEditor, cleanStaff } from '../../components/StaffEditor'
import { Button, ErrorState, PageHeader, Panel, Spinner, cx } from '../../components/ui'
import { toast } from '../../components/toast'
import { ru } from '../../i18n/ru'

/** "Услуги и мастера": the price list and the staff with working days the bot answers from. */
export function Knowledge() {
  const { data, error, loading, reload, setData } = useLoad(() => api.get<BotSettings>('/api/settings/bot', { silent: true }))
  const [form, setForm] = useState<Pick<BotSettings, 'services' | 'staff'> | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (data) setForm({ services: structuredClone(data.services ?? []), staff: structuredClone(data.staff ?? []) })
  }, [data])

  const dirty = useMemo(
    () => !!form && !!data && JSON.stringify(form) !== JSON.stringify({ services: data.services ?? [], staff: data.staff ?? [] }),
    [form, data],
  )

  useEffect(() => {
    if (!dirty) return
    const h = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', h)
    return () => window.removeEventListener('beforeunload', h)
  }, [dirty])

  if (loading && !form) return <PageBody><Spinner /></PageBody>
  if (error && !form) return <PageBody><ErrorState message={error.message} onRetry={reload} /></PageBody>
  if (!form || !data) return null

  const clinic = data.template_id === 'clinic'
  const serviceNames = form.services.map((s) => s.name.trim()).filter(Boolean)

  async function save() {
    if (!form || !data) return
    setSaving(true)
    try {
      // PUT replaces the whole object: re-read so other settings changed meanwhile are not overwritten
      const fresh = await api.get<BotSettings>('/api/settings/bot')
      const saved = await api.put<BotSettings>('/api/settings/bot', {
        ...fresh,
        services: cleanServices(form.services),
        staff: cleanStaff(form.staff),
      })
      setData(saved)
      toast.success(ru.common.saved)
    } catch {
      /* toast */
    } finally {
      setSaving(false)
    }
  }

  return (
    <PageBody>
      <PageHeader
        title={ru.nav.knowledge}
        description="Отсюда бот берёт цены, длительность и кто из специалистов в какой день работает. Запись по-прежнему подтверждает администратор."
        actions={
          <Link to="/test-chat" className="inline-flex h-9 items-center gap-1.5 rounded-[7px] border border-line-strong bg-surface px-3.5 text-sm font-medium text-ink hover:bg-sunken">
            <FlaskConical className="size-4" aria-hidden />
            Проверить бота
          </Link>
        }
      />
      <form
        className="flex flex-col gap-5 pb-24"
        onSubmit={(e) => {
          e.preventDefault()
          void save()
        }}
      >
        {/* 2xl+: price list and staff side by side */}
        <div className="grid items-start gap-5 2xl:grid-cols-2">
          <Panel className="min-w-0" title="Услуги и цены" description="Цена может быть «от» или диапазоном. Пустая цена — бот скажет, что её уточнит администратор.">
            <ServicesEditor value={form.services} onChange={(services) => setForm((f) => (f ? { ...f, services } : f))} />
          </Panel>
          <Panel
            className="min-w-0"
            title={clinic ? 'Врачи' : 'Мастера'}
            description="Бот ответит, работает ли специалист в нужный день и до скольки, и примет заявку. Свободное время он не видит — время подтверждает администратор."
          >
            <StaffEditor
              value={form.staff}
              serviceNames={serviceNames}
              title={clinic ? 'врачей' : 'мастеров'}
              onChange={(staff) => setForm((f) => (f ? { ...f, staff } : f))}
            />
          </Panel>
        </div>

        <div
          className={cx(
            'sticky bottom-4 z-10 flex flex-wrap items-center justify-between gap-3 rounded-[10px] border px-4 py-3 transition-[opacity,transform] duration-200 ease-out',
            dirty ? 'border-line-strong bg-surface shadow-[var(--shadow-pop)]' : 'pointer-events-none translate-y-2 border-transparent opacity-0',
          )}
          aria-hidden={!dirty}
        >
          <span className="text-sm text-ink-2">{ru.common.unsaved}</span>
          <div className="flex gap-2">
            <Button
              variant="ghost"
              tabIndex={dirty ? 0 : -1}
              onClick={() => setForm({ services: structuredClone(data.services ?? []), staff: structuredClone(data.staff ?? []) })}
            >
              {ru.common.discard}
            </Button>
            <Button type="submit" variant="primary" loading={saving} tabIndex={dirty ? 0 : -1}>
              {ru.common.save}
            </Button>
          </div>
        </div>
      </form>
    </PageBody>
  )
}
