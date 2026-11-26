import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { api, ApiError, type StaffMe } from '../../api'

/** Current staff member; redirects to /staff/login when the session is missing or is a client session. */
export function useStaff() {
  const navigate = useNavigate()
  const [staff, setStaff] = useState<StaffMe | null>(null)

  useEffect(() => {
    api
      .get<StaffMe>('/api/staff/auth/me')
      .then(setStaff)
      .catch((err) => {
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) navigate('/staff/login', { replace: true })
      })
  }, [navigate])

  const logout = useCallback(async () => {
    await api.post('/api/staff/auth/logout').catch(() => undefined)
    navigate('/staff/login', { replace: true })
  }, [navigate])

  return { staff, logout }
}
