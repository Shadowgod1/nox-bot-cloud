/**
 * A tiny Markdown renderer for the legal pages (headings, paragraphs, lists, tables, **bold**, `code`, bare https
 * links). It builds React elements only — no HTML is injected — so the text cannot add markup or scripts.
 */
import type { ReactNode } from 'react'
import type { Seller } from '../api'

type Block =
  | { kind: 'h'; level: number; text: string }
  | { kind: 'p'; lines: string[] }
  | { kind: 'ul' | 'ol'; items: string[] }
  | { kind: 'table'; head: string[]; rows: string[][] }

const HEADING = /^(#{1,3})\s+(.*)$/
const UL = /^[-*]\s+(.*)$/
const OL = /^\d+\.\s+(.*)$/

function cells(line: string): string[] {
  return line
    .trim()
    .replace(/^\||\|$/g, '')
    .split('|')
    .map((c) => c.trim())
}

export function parse(md: string): Block[] {
  const blocks: Block[] = []
  const lines = md.replace(/\r\n?/g, '\n').split('\n')
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) {
      i++
      continue
    }
    const h = HEADING.exec(line)
    if (h) {
      blocks.push({ kind: 'h', level: h[1].length, text: h[2].trim() })
      i++
      continue
    }
    if (line.trim().startsWith('|')) {
      const rows: string[][] = []
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        const row = cells(lines[i])
        if (!row.every((c) => /^:?-{2,}:?$/.test(c))) rows.push(row)
        i++
      }
      blocks.push({ kind: 'table', head: rows[0] ?? [], rows: rows.slice(1) })
      continue
    }
    const list = UL.test(line) ? 'ul' : OL.test(line) ? 'ol' : null
    if (list) {
      const re = list === 'ul' ? UL : OL
      const items: string[] = []
      while (i < lines.length && re.test(lines[i])) {
        items.push(re.exec(lines[i])![1])
        i++
      }
      blocks.push({ kind: list, items })
      continue
    }
    const para: string[] = []
    while (i < lines.length && lines[i].trim() && !HEADING.test(lines[i]) && !lines[i].trim().startsWith('|') && !UL.test(lines[i]) && !OL.test(lines[i])) {
      para.push(lines[i].trim())
      i++
    }
    blocks.push({ kind: 'p', lines: para })
  }
  return blocks
}

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|https?:\/\/[^\s<>()«»"]+[^\s<>()«»".,;:!?])/g

export function inline(text: string): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  let key = 0
  for (const m of text.matchAll(INLINE)) {
    const token = m[0]
    if (m.index > last) out.push(text.slice(last, m.index))
    if (token.startsWith('**')) out.push(<strong key={key++} className="font-bold text-ink">{token.slice(2, -2)}</strong>)
    else if (token.startsWith('`')) out.push(<code key={key++} className="rounded bg-sunken px-1 font-mono text-[0.92em]">{token.slice(1, -1)}</code>)
    else
      out.push(
        <a key={key++} href={token} className="break-all font-semibold text-accent hover:text-accent-hover" rel="noopener">
          {token.replace(/^https?:\/\//, '')}
        </a>,
      )
    last = m.index + token.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export function Markdown({ source }: { source: string }) {
  return (
    <div className="nx-legal flex flex-col gap-3 text-[15px] leading-relaxed text-ink-2">
      {parse(source).map((b, n) => {
        switch (b.kind) {
          case 'h':
            if (b.level === 1)
              return (
                <h1 key={n} className="text-[24px] leading-tight font-extrabold tracking-[-0.015em] text-ink sm:text-[28px]">
                  {inline(b.text)}
                </h1>
              )
            return (
              <h2 key={n} className="mt-4 text-[18px] font-extrabold text-ink">
                {inline(b.text)}
              </h2>
            )
          case 'p':
            return (
              <p key={n}>
                {b.lines.map((l, j) => (
                  <span key={j}>
                    {j > 0 && <br />}
                    {inline(l)}
                  </span>
                ))}
              </p>
            )
          case 'ul':
          case 'ol': {
            const Tag = b.kind
            return (
              <Tag key={n} className={b.kind === 'ul' ? 'flex list-disc flex-col gap-1.5 pl-5' : 'flex list-decimal flex-col gap-1.5 pl-5'}>
                {b.items.map((it, j) => (
                  <li key={j}>{inline(it)}</li>
                ))}
              </Tag>
            )
          }
          case 'table':
            return (
              <div key={n} className="overflow-x-auto rounded-nx border border-line">
                <table className="w-full min-w-[520px] text-left text-[13.5px]">
                  <thead className="bg-sunken text-ink">
                    <tr>
                      {b.head.map((c, j) => (
                        <th key={j} className="px-3 py-2 font-bold">
                          {inline(c)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {b.rows.map((r, j) => (
                      <tr key={j} className="border-t border-line align-top">
                        {r.map((c, k) => (
                          <td key={k} className="px-3 py-2">
                            {inline(c)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
        }
      })}
    </div>
  )
}

/** Fill the "[____]" requisites the portal knows (NOX_SELLER_*); unknown ones stay in brackets. */
export function fillRequisites(md: string, seller: Partial<Seller> | undefined, phone?: string): string {
  const s = seller ?? {}
  const rules: [RegExp, string | undefined][] = [
    [/\[Полное наименование: [^\]]*\]/g, s.name],
    [/\[Наименование ТОО\/ИП\]/g, s.name],
    [/\[Наименование\]/g, s.name],
  ]
  let out = md
  for (const [re, value] of rules) if (value) out = out.replace(re, value)
  const labelled: [string, string | undefined][] = [
    ['БИН/ИИН', s.bin],
    ['БИН', s.bin],
    ['адрес', s.address],
    ['Адрес:', s.address],
    ['Банк:', s.bank],
    ['ИИК', s.iik],
    ['БИК', s.bik],
    ['Кбе', s.kbe],
    ['Телефон:', s.phone || phone],
    ['телефон', s.phone || phone],
  ]
  for (const [label, value] of labelled) {
    if (!value) continue
    const escaped = label.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')
    out = out.replace(new RegExp(`(^|[\\s(,])${escaped} \\[[^\\]]*\\]`, 'g'), (_m, pre: string) => `${pre}${label} ${value}`)
  }
  return out
}
