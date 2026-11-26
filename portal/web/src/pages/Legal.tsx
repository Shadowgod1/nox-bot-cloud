import { useEffect } from 'react'
import { FileText, TriangleAlert } from 'lucide-react'
import { Shell } from '../components/Shell'
import { usePublicConfig } from '../components/Support'
import { Card, cx } from '../components/ui'
import { fillRequisites, Markdown } from '../legal/markdown'
import offer from '../legal/offer.md?raw'
import privacy from '../legal/privacy.md?raw'
import refund from '../legal/refund.md?raw'

export type LegalDoc = 'offer' | 'refund' | 'privacy'

const DOCS: Record<LegalDoc, { title: string; source: string }> = {
  offer: { title: 'Публичная оферта', source: offer },
  refund: { title: 'Политика возврата', source: refund },
  privacy: { title: 'Политика конфиденциальности', source: privacy },
}

/** /offer, /refund, /privacy: the documents with the requisites the portal knows; "Черновик" until NOX_LEGAL_DATE. */
export function Legal({ doc }: { doc: LegalDoc }) {
  const config = usePublicConfig()
  const { title, source } = DOCS[doc]
  useEffect(() => {
    document.title = `${title} — Nox Bot`
  }, [title])
  const date = config?.legal_date ?? ''
  const text = fillRequisites(source, config?.seller, config?.support?.phone)
  return (
    <Shell subtitle="Документы">
      <div
        role="note"
        className={cx(
          'mb-4 flex items-center gap-2 rounded-nx px-3 py-2 text-[13.5px] font-semibold',
          date ? 'bg-accent-soft text-accent-ink' : 'bg-warn-soft text-warn',
        )}
        data-testid="legal-edition"
      >
        {date ? <FileText className="size-4 flex-none" aria-hidden /> : <TriangleAlert className="size-4 flex-none" aria-hidden />}
        {date ? `Редакция от ${date}` : 'Черновик: текст ещё может измениться'}
      </div>
      <Card className="p-5 sm:p-8">
        <Markdown source={text} />
      </Card>
    </Shell>
  )
}
