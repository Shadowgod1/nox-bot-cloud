import { useCallback, useSyncExternalStore } from 'react'

/** True while the media query matches; false where matchMedia is missing (tests, very old browsers). */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (fn: () => void) => {
      if (typeof window.matchMedia !== 'function') return () => {}
      const mql = window.matchMedia(query)
      mql.addEventListener('change', fn)
      return () => mql.removeEventListener('change', fn)
    },
    [query],
  )
  const get = () => typeof window.matchMedia === 'function' && window.matchMedia(query).matches
  return useSyncExternalStore(subscribe, get, () => false)
}

/** Tailwind `xl`: from here the conversations screen has room for the client panel as a third column. */
export const XL_QUERY = '(min-width: 1280px)'
