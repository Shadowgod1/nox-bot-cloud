import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { api, ApiError } from '../api/client'
import type { BrandInfo, SetupState, User } from '../api/types'
import { isBrand, setBrand } from '../brand'

type Phase = 'checking' | 'setup' | 'anonymous' | 'ready' | 'offline'

interface AuthValue {
  phase: Phase
  user: User | null
  isOwner: boolean
  /** Re-run setup/state + auth/me (after login or the setup wizard). */
  refresh: () => Promise<void>
  setUser: (u: User | null) => void
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthValue | null>(null)

const PUBLIC = ['/login', '/setup']
const OFFLINE_CODES = ['network', 'core_unavailable']

/** Ask the server for the deploy-time brand; on any failure keep the cached one. */
async function syncBrand() {
  try {
    const r = await api.get<BrandInfo>('/api/brand', { silent: true, noAuthRedirect: true })
    if (isBrand(r?.brand)) setBrand(r.brand)
  } catch {
    /* older core or offline: the cached brand stays */
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>('checking')
  const [user, setUser] = useState<User | null>(null)
  const navigate = useNavigate()
  const location = useLocation()

  const refresh = useCallback(async () => {
    void syncBrand()
    try {
      const st = await api.get<SetupState>('/api/setup/state', { silent: true })
      if (st.needs_setup) {
        setUser(null)
        setPhase('setup')
        return
      }
    } catch (e) {
      if (e instanceof ApiError && OFFLINE_CODES.includes(e.code)) {
        setPhase('offline')
        return
      }
      // Older core or transient error: fall through to the session probe.
    }
    try {
      const me = await api.get<User>('/api/auth/me', { silent: true, noAuthRedirect: true })
      setUser(me)
      setPhase('ready')
    } catch (e) {
      setUser(null)
      setPhase(e instanceof ApiError && OFFLINE_CODES.includes(e.code) ? 'offline' : 'anonymous')
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // Route guards driven by phase.
  useEffect(() => {
    const path = location.pathname
    if (phase === 'setup' && path !== '/setup') navigate('/setup', { replace: true })
    else if (phase === 'anonymous' && !PUBLIC.includes(path)) navigate('/login', { replace: true })
  }, [phase, location.pathname, navigate])

  const logout = useCallback(async () => {
    try {
      await api.post('/api/auth/logout', {}, { silent: true })
    } catch {
      /* the session is gone either way */
    }
    setUser(null)
    setPhase('anonymous')
    navigate('/login', { replace: true })
  }, [navigate])

  const value = useMemo<AuthValue>(
    () => ({ phase, user, isOwner: user?.role === 'owner', refresh, setUser, logout }),
    [phase, user, refresh, logout],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const v = useContext(AuthContext)
  if (!v) throw new Error('useAuth outside AuthProvider')
  return v
}
