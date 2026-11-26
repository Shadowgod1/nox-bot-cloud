import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { ArrowLeft } from 'lucide-react'
import { api, type AuditOut } from '../../api'
import { Shell } from '../../components/Shell'
import { Alert, Card } from '../../components/ui'
import { AuditList } from './ApplicationView'
import { useStaff } from './useStaff'

export function AuditPage() {
  const { staff, logout } = useStaff()
  const [rows, setRows] = useState<AuditOut[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!staff) return
    api
      .get<AuditOut[]>('/api/staff/audit?limit=300')
      .then(setRows)
      .catch((err) => setError(err instanceof Error ? err.message : 'Ошибка загрузки'))
  }, [staff])

  return (
    <Shell subtitle="Журнал" home="/staff" user={staff?.email} onLogout={logout} wide>
      <Link to="/staff" className="inline-flex items-center gap-1 text-[13.5px] font-semibold text-ink-2 hover:text-accent">
        <ArrowLeft className="size-4" aria-hidden /> Все заявки
      </Link>
      <h1 className="mt-3 text-[22px] font-extrabold tracking-tight text-ink">Журнал действий</h1>
      {error && (
        <div className="mt-4">
          <Alert>{error}</Alert>
        </div>
      )}
      <Card className="mt-4 p-5">{rows && <AuditList rows={rows} />}</Card>
    </Shell>
  )
}
