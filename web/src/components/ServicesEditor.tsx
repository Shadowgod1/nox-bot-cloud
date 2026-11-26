import { useState } from 'react'
import { CircleAlert, Plus, Trash2, Upload } from 'lucide-react'
import type { ServiceItem } from '../api/types'
import { PriceImport } from './PriceImport'
import { Button, Input, cx } from './ui'

export const SERVICES_MAX = 300

/** A template placeholder ("от … ₸") or an empty price: the bot says "the administrator will tell". */
export function priceMissing(price: string): boolean {
  return !/\d/.test(price) || price.includes('…') || price.includes('...')
}

/** "5000" -> "5 000 ₸", "от 7000" -> "от 7 000 ₸"; anything else is kept as typed. */
export function normalizePrice(v: string): string {
  const m = v.trim().match(/^(от\s*)?(\d[\d\s]*)\s*(₸|тг|тенге)?$/i)
  if (!m) return v.trim()
  const amount = Number(m[2].replace(/\s/g, ''))
  if (!amount) return v.trim()
  return `${m[1] ? 'от ' : ''}${amount.toLocaleString('ru-RU').replace(/ /g, ' ')} ₸`
}

/** Editable price list: name, duration (minutes), price. Rows with a placeholder price are highlighted. */
export function ServicesEditor({ value, onChange }: { value: ServiceItem[]; onChange: (v: ServiceItem[]) => void }) {
  const [importing, setImporting] = useState(false)
  const missing = value.filter((s) => s.name.trim() && priceMissing(s.price)).length
  const setRow = (i: number, patch: Partial<ServiceItem>) => onChange(value.map((r, k) => (k === i ? { ...r, ...patch } : r)))

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant={value.length ? 'secondary' : 'primary'} onClick={() => setImporting(true)} disabled={importing} icon={<Upload className="size-3.5" aria-hidden />}>
          Загрузить прайс
        </Button>
        <span className="text-[13px] text-ink-2">Excel, CSV, текст или фото — перед сохранением покажем, что нашли.</span>
      </div>

      {importing && (
        <PriceImport
          hasServices={value.some((s) => s.name.trim())}
          onClose={() => setImporting(false)}
          onApply={(rows, mode) => {
            const base = mode === 'replace' ? [] : value.filter((s) => s.name.trim())
            const known = new Set(base.map((s) => s.name.trim().toLowerCase()))
            const merged = [...base]
            for (const r of rows) {
              const key = r.name.trim().toLowerCase()
              const at = merged.findIndex((s) => s.name.trim().toLowerCase() === key)
              if (known.has(key) && at >= 0) merged[at] = { ...merged[at], ...r } // same service: the new price wins
              else merged.push(r)
            }
            onChange(merged.slice(0, SERVICES_MAX))
            setImporting(false)
          }}
        />
      )}

      {missing > 0 && (
        <p role="status" className="flex items-start gap-2 text-[13px] text-warn">
          <CircleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          Не заполнено цен: {missing}. Пока цены нет, бот отвечает «цену уточнит администратор».
        </p>
      )}

      {value.length > 0 ? (
        <ul aria-label="Услуги и цены" className="divide-y divide-line rounded-[9px] border border-line">
          <li aria-hidden className="hidden grid-cols-[minmax(0,1fr)_96px_150px_36px] gap-2 px-3 py-2 text-[12px] text-ink-3 sm:grid">
            <span>Услуга</span>
            <span>Минут</span>
            <span>Цена</span>
            <span />
          </li>
          {value.map((s, i) => (
            <li key={i} className="grid grid-cols-[minmax(0,1fr)_96px_36px] gap-2 px-3 py-2 sm:grid-cols-[minmax(0,1fr)_96px_150px_36px]">
              <Input
                aria-label={`Услуга ${i + 1}`}
                value={s.name}
                maxLength={160}
                onChange={(e) => setRow(i, { name: e.target.value })}
                placeholder="Название услуги"
                className="col-span-2 sm:col-span-1"
              />
              <div className="relative max-sm:order-4">
                <Input
                  aria-label={`${s.name || `Услуга ${i + 1}`}: длительность, минут`}
                  type="number"
                  min={1}
                  max={1440}
                  value={s.duration_min ?? ''}
                  onChange={(e) => setRow(i, { duration_min: e.target.value ? Math.min(1440, Math.max(1, Number(e.target.value))) : null })}
                  className="tabular pr-10"
                />
                <span aria-hidden className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-[12px] text-ink-3">
                  мин
                </span>
              </div>
              <Input
                aria-label={`${s.name || `Услуга ${i + 1}`}: цена`}
                value={s.price}
                maxLength={60}
                onChange={(e) => setRow(i, { price: e.target.value })}
                onBlur={(e) => setRow(i, { price: normalizePrice(e.target.value) })}
                placeholder="от 5 000 ₸"
                aria-invalid={priceMissing(s.price) || undefined}
                className={cx('tabular max-sm:order-3', priceMissing(s.price) && 'border-warn/60 bg-warn-soft/40')}
              />
              <Button
                size="sm"
                variant="danger-ghost"
                className="h-9 max-sm:order-2"
                aria-label={`Удалить «${s.name || `услугу ${i + 1}`}»`}
                onClick={() => onChange(value.filter((_, k) => k !== i))}
                icon={<Trash2 className="size-3.5" aria-hidden />}
              />
            </li>
          ))}
        </ul>
      ) : (
        !importing && <p className="rounded-[9px] border border-dashed border-line-strong px-4 py-5 text-center text-sm text-ink-2">Услуг пока нет. Загрузите прайс или добавьте услуги вручную.</p>
      )}

      <Button
        size="sm"
        variant="ghost"
        className="self-start"
        disabled={value.length >= SERVICES_MAX}
        onClick={() => onChange([...value, { name: '', price: '', duration_min: null }])}
        icon={<Plus className="size-3.5" aria-hidden />}
      >
        Добавить услугу
      </Button>
    </div>
  )
}

/** Rows that can be saved: named, trimmed. */
export function cleanServices(value: ServiceItem[]): ServiceItem[] {
  return value
    .filter((s) => s.name.trim())
    .map((s) => ({ name: s.name.trim(), price: s.price.trim(), duration_min: s.duration_min || null }))
}
