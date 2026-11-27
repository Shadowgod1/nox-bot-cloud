import { useSyncExternalStore } from 'react'

/**
 * Brand of the admin UI, picked at deploy time (core setting ILER_BRAND, served by GET /api/brand):
 * "iler" for on-prem installs, "nox" for the cloud edition run by Nox service.
 *
 * public/brand-init.js applies the cached brand and theme to <html> before the first paint
 * (data-brand, data-theme, title, favicon); App asks the server and corrects it with setBrand().
 * Only the Nox brand has a light/dark theme; ILER always renders its single light theme.
 */

export type Brand = 'iler' | 'nox'
export type Theme = 'light' | 'dark'

export interface BrandCopy {
  id: Brand
  /** Product name in UI copy: "Вход в ILER", "Коины Nox". */
  name: string
  /** Company to contact: "обратитесь в ILER", "обратитесь в Nox service". */
  company: string
  title: string
  favicon: string
  /** The brand has the light/dark switch. */
  themes: boolean
}

export const BRANDS: Record<Brand, BrandCopy> = {
  iler: { id: 'iler', name: 'ILER', company: 'ILER', title: 'ILER', favicon: '/favicon.svg', themes: false },
  nox: { id: 'nox', name: 'Nox', company: 'Nox service', title: 'Nox Bot', favicon: '/favicon-nox.svg', themes: true },
}

// localStorage keys, shared with public/brand-init.js
export const BRAND_KEY = 'iler.brand'
export const THEME_KEY = 'iler.theme'

export function isBrand(v: unknown): v is Brand {
  return v === 'iler' || v === 'nox'
}

function store(): Storage | null {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

function read(key: string): string | null {
  try {
    return store()?.getItem(key) ?? null
  } catch {
    return null
  }
}

function write(key: string, value: string) {
  try {
    store()?.setItem(key, value)
  } catch {
    /* private mode or blocked storage: the choice just is not remembered */
  }
}

function initialBrand(): Brand {
  const fromHtml = document.documentElement.getAttribute('data-brand')
  if (isBrand(fromHtml)) return fromHtml
  const cached = read(BRAND_KEY)
  return isBrand(cached) ? cached : 'iler'
}

function initialTheme(): Theme {
  return read(THEME_KEY) === 'dark' ? 'dark' : 'light'
}

let brand: Brand = initialBrand()
let theme: Theme = initialTheme()
const listeners = new Set<() => void>()

function emit() {
  listeners.forEach((fn) => fn())
}

function subscribe(fn: () => void) {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

/** Write brand and theme to <html>, the title and the favicon. */
function apply() {
  const html = document.documentElement
  const copy = BRANDS[brand]
  html.setAttribute('data-brand', brand)
  if (copy.themes) html.setAttribute('data-theme', theme)
  else html.removeAttribute('data-theme')
  document.title = copy.title
  const icon = document.querySelector<HTMLLinkElement>('link[rel="icon"]')
  if (icon && icon.getAttribute('href') !== copy.favicon) icon.setAttribute('href', copy.favicon)
}

apply()

export function getBrand(): Brand {
  return brand
}

/** Copy of the current brand, for code outside React (API client error texts). */
export function brandCopy(): BrandCopy {
  return BRANDS[brand]
}

export function setBrand(next: Brand) {
  write(BRAND_KEY, next)
  if (next === brand) return
  brand = next
  apply()
  emit()
}

export function getTheme(): Theme {
  return theme
}

export function setTheme(next: Theme) {
  write(THEME_KEY, next)
  if (next === theme) return
  theme = next
  apply()
  emit()
}

export function useBrand(): BrandCopy {
  return BRANDS[useSyncExternalStore(subscribe, getBrand, getBrand)]
}

export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, getTheme, getTheme)
}
