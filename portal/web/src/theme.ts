import { useCallback, useEffect, useState } from 'react'

export type Theme = 'light' | 'dark'
const KEY = 'nox-theme'

function stored(): Theme | null {
  try {
    const v = localStorage.getItem(KEY)
    return v === 'light' || v === 'dark' ? v : null
  } catch {
    return null
  }
}

export function initialTheme(): Theme {
  const saved = stored()
  if (saved) return saved
  // the Nox brand is dark (as noxsw.kz); light only when the visitor picks it with the toggle
  return 'dark'
}

export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => initialTheme())
  useEffect(() => applyTheme(theme), [theme])
  const toggle = useCallback(() => {
    setTheme((t) => {
      const next: Theme = t === 'dark' ? 'light' : 'dark'
      try {
        localStorage.setItem(KEY, next)
      } catch {
        /* private mode: the choice lasts for this page only */
      }
      return next
    })
  }, [])
  return [theme, toggle]
}

/** The theme currently on <html>, kept in sync with the toggle (which may live in another component). */
export function useThemeValue(): Theme {
  const read = (): Theme => (document.documentElement.dataset.theme === 'light' ? 'light' : 'dark')
  const [theme, setTheme] = useState<Theme>(read)
  useEffect(() => {
    const obs = new MutationObserver(() => setTheme(read()))
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    setTheme(read())
    return () => obs.disconnect()
  }, [])
  return theme
}
