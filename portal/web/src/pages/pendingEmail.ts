// The e-mail being verified travels in router state, with sessionStorage as a fallback for reloads
// (never in the URL).
const KEY = 'nox-pending-email'

export function rememberEmail(email: string) {
  try {
    sessionStorage.setItem(KEY, email)
  } catch {
    /* ignore */
  }
}

export function recallEmail(): string {
  try {
    return sessionStorage.getItem(KEY) ?? ''
  } catch {
    return ''
  }
}

export function forgetEmail() {
  try {
    sessionStorage.removeItem(KEY)
  } catch {
    /* ignore */
  }
}
