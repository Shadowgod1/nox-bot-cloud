import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'
import { setBrand, setTheme } from '../brand'

afterEach(() => {
  cleanup()
  setBrand('iler')
  setTheme('light')
  try {
    window.localStorage.clear()
  } catch {
    /* no storage */
  }
  document.cookie.split(';').forEach((c) => {
    const name = c.split('=')[0]?.trim()
    if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`
  })
})

// jsdom has no layout: scrolling is a no-op.
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}
