import { useRef, useState } from 'react'
import { Camera, ClipboardPaste, FileSpreadsheet, Trash2, Upload, X } from 'lucide-react'
import { api } from '../api/client'
import type { PriceImportResult, PriceImportSupport, ServiceItem } from '../api/types'
import { useLoad } from '../hooks/useLoad'
import { Button, Input, Segmented, Textarea } from './ui'

type Mode = 'file' | 'text' | 'photo'

const MAX_BYTES = 5 * 1024 * 1024
const TEXT_EXAMPLE = 'Маникюр — 5000\nМаникюр с покрытием — от 7000, 90 мин\nНаращивание ресниц 2D — 12 000 тг, 2 ч'

/** File -> base64 without data: prefix, in chunks (large files overflow String.fromCharCode). */
export async function fileToBase64(file: Blob): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer())
  let bin = ''
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(bin)
}

/**
 * Price list import: a CSV / XLSX file, pasted text, or a photo (read by a vision model on the server).
 * The server only parses; the rows come back as an editable preview and are applied by `onApply`.
 */
export function PriceImport({
  hasServices,
  onApply,
  onClose,
}: {
  hasServices: boolean
  onApply: (rows: ServiceItem[], mode: 'append' | 'replace') => void
  onClose: () => void
}) {
  const support = useLoad(() => api.get<PriceImportSupport>('/api/knowledge/import-prices', { silent: true }))
  const [mode, setMode] = useState<Mode>('file')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<PriceImportResult | null>(null)
  const [rows, setRows] = useState<ServiceItem[]>([])
  const fileRef = useRef<HTMLInputElement>(null)
  const photoRef = useRef<HTMLInputElement>(null)

  const photoOk = support.data?.photo ?? true

  async function run(body: Record<string, unknown>) {
    setBusy(true)
    setError(null)
    try {
      const r = await api.post<PriceImportResult>('/api/knowledge/import-prices', body, { silent: true })
      setResult(r)
      setRows(r.rows)
    } catch (e) {
      setError((e as Error).message || 'Не удалось прочитать прайс')
    } finally {
      setBusy(false)
    }
  }

  async function pickFile(file: File | undefined) {
    if (!file) return
    if (file.size > MAX_BYTES) {
      setError('Файл больше 5 МБ')
      return
    }
    await run({ filename: file.name, content_base64: await fileToBase64(file) })
  }

  const setRow = (i: number, patch: Partial<ServiceItem>) => setRows((rs) => rs.map((r, k) => (k === i ? { ...r, ...patch } : r)))
  const valid = rows.filter((r) => r.name.trim())

  return (
    <section aria-label="Импорт прайса" className="rounded-[10px] border border-accent/40 bg-accent-soft/40 p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-ink">Загрузить прайс</h3>
        <Button size="sm" variant="ghost" onClick={onClose} aria-label="Закрыть импорт" icon={<X className="size-4" aria-hidden />} />
      </div>

      {!result ? (
        <div className="flex flex-col gap-3">
          <Segmented<Mode>
            label="Откуда взять прайс"
            value={mode}
            onChange={(m) => {
              setMode(m)
              setError(null)
            }}
            options={[
              { value: 'file', label: 'Файл Excel/CSV' },
              { value: 'text', label: 'Вставить текст' },
              { value: 'photo', label: 'Фото прайса' },
            ]}
          />
          {mode === 'file' && (
            <div className="flex flex-col items-start gap-2">
              <p className="text-[13px] text-ink-2">
                Таблица с колонками «Услуга», «Цена» и, если есть, «Длительность». Заголовки можно по-русски, порядок любой.
              </p>
              <input
                ref={fileRef}
                type="file"
                accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                className="sr-only"
                aria-label="Файл прайса"
                onChange={(e) => {
                  void pickFile(e.target.files?.[0])
                  e.target.value = ''
                }}
              />
              <Button loading={busy} onClick={() => fileRef.current?.click()} icon={<FileSpreadsheet className="size-4" aria-hidden />}>
                Выбрать файл
              </Button>
            </div>
          )}
          {mode === 'text' && (
            <div className="flex flex-col gap-2">
              <label htmlFor="price-text" className="text-[13px] text-ink-2">
                По одной услуге на строку: название, цена и, если есть, длительность. Можно скопировать прямо из Excel.
              </label>
              <Textarea id="price-text" rows={6} value={text} onChange={(e) => setText(e.target.value)} placeholder={TEXT_EXAMPLE} />
              <Button
                className="self-start"
                variant="primary"
                loading={busy}
                disabled={!text.trim()}
                onClick={() => void run({ text })}
                icon={<ClipboardPaste className="size-4" aria-hidden />}
              >
                Разобрать
              </Button>
            </div>
          )}
          {mode === 'photo' && (
            <div className="flex flex-col items-start gap-2">
              {photoOk ? (
                <>
                  <p className="text-[13px] text-ink-2">
                    Сфотографируйте прайс ровно и близко (JPG, PNG, до 5 МБ). Распознаёт модель — это стоит немного коинов. Проверьте результат перед сохранением.
                  </p>
                  <input
                    ref={photoRef}
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    className="sr-only"
                    aria-label="Фото прайса"
                    onChange={(e) => {
                      void pickFile(e.target.files?.[0])
                      e.target.value = ''
                    }}
                  />
                  <Button loading={busy} onClick={() => photoRef.current?.click()} icon={<Camera className="size-4" aria-hidden />}>
                    {busy ? 'Распознаём…' : 'Выбрать фото'}
                  </Button>
                </>
              ) : (
                <p className="text-[13px] text-ink-2">
                  Распознавание фото недоступно на этом сервере. Загрузите файл или вставьте прайс текстом.
                </p>
              )}
            </div>
          )}
          {error && (
            <p role="alert" className="text-[13px] text-danger">
              {error}
            </p>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="text-[13px] text-ink-2">
            Нашли {rows.length} {rows.length === 1 ? 'услугу' : rows.length >= 2 && rows.length <= 4 ? 'услуги' : 'услуг'}. Поправьте названия и цены, лишнее удалите.
            {result.coins ? ` Распознавание стоило ${result.coins} ${coinsWord(result.coins)}.` : ''}
          </p>
          {result.warnings.map((w) => (
            <p key={w} className="text-[13px] text-warn">
              {w}
            </p>
          ))}
          {rows.length > 0 && (
            <ul aria-label="Найденные услуги" className="max-h-[360px] divide-y divide-line overflow-y-auto rounded-[8px] border border-line bg-surface">
              {rows.map((r, i) => (
                <li key={i} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 px-2.5 py-2 sm:grid-cols-[minmax(0,1fr)_120px_90px_auto]">
                  <Input aria-label={`Услуга ${i + 1}: название`} value={r.name} onChange={(e) => setRow(i, { name: e.target.value })} className="max-sm:col-span-1" />
                  <Button
                    size="sm"
                    variant="danger-ghost"
                    className="sm:order-last"
                    aria-label={`Удалить «${r.name}»`}
                    onClick={() => setRows((rs) => rs.filter((_, k) => k !== i))}
                    icon={<Trash2 className="size-3.5" aria-hidden />}
                  />
                  <Input aria-label={`Услуга ${i + 1}: цена`} value={r.price} onChange={(e) => setRow(i, { price: e.target.value })} placeholder="5 000 ₸" className="tabular" />
                  <Input
                    aria-label={`Услуга ${i + 1}: минут`}
                    type="number"
                    min={1}
                    max={1440}
                    value={r.duration_min ?? ''}
                    placeholder="мин"
                    onChange={(e) => setRow(i, { duration_min: e.target.value ? Math.min(1440, Math.max(1, Number(e.target.value))) : null })}
                    className="tabular"
                  />
                </li>
              ))}
            </ul>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" disabled={!valid.length} onClick={() => onApply(valid, hasServices ? 'append' : 'replace')} icon={<Upload className="size-4" aria-hidden />}>
              {hasServices ? 'Добавить к списку' : 'Перенести в список'}
            </Button>
            {hasServices && (
              <Button disabled={!valid.length} onClick={() => onApply(valid, 'replace')}>
                Заменить весь список
              </Button>
            )}
            <Button variant="ghost" onClick={() => setResult(null)}>
              Загрузить другой
            </Button>
          </div>
        </div>
      )}
    </section>
  )
}

export function coinsWord(n: number): string {
  const whole = Math.floor(Math.abs(n))
  if (whole !== Math.abs(n)) return 'коина'
  const m10 = whole % 10
  const m100 = whole % 100
  if (m10 === 1 && m100 !== 11) return 'коин'
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return 'коина'
  return 'коинов'
}
