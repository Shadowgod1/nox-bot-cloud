import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import { ApiError } from '../api/client'

export interface LoadState<T> {
  data: T | undefined
  error: ApiError | null
  loading: boolean
  reload: () => Promise<void>
  setData: Dispatch<SetStateAction<T | undefined>>
}

/** Load data on mount / when deps change. Errors are rendered by the page (requests are silent). */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[] = []): LoadState<T> {
  const [data, setData] = useState<T>()
  const [error, setError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(true)
  const seq = useRef(0)
  const fnRef = useRef(fn)
  fnRef.current = fn

  const reload = useCallback(async () => {
    const my = ++seq.current
    setLoading(true)
    setError(null)
    try {
      const d = await fnRef.current()
      if (my === seq.current) setData(d)
    } catch (e) {
      if (my === seq.current) setError(e instanceof ApiError ? e : new ApiError(0, 'error', String(e)))
    } finally {
      if (my === seq.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return { data, error, loading, reload, setData }
}
