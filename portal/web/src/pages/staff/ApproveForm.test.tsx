import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { bodyOf, json, mockFetch } from '../../test/utils'
import { ApproveForm } from './ApproveForm'

const config = { coin_presets: [30000, 100000, 300000], instance_domain: 'example.kz', custom_key_supported: true }
const URL_ = 'POST /api/staff/applications/7/approve'

function setup(cfg = config) {
  const onDone = vi.fn()
  render(<ApproveForm applicationId={7} defaultSlug="primer" config={cfg} onDone={onDone} />)
  return onDone
}

describe('ApproveForm', () => {
  it('approves with the first preset and no key by default', async () => {
    const fetch = mockFetch({ [URL_]: () => json({ ok: true }) })
    document.cookie = 'nox_csrf=tok123'
    const onDone = setup()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Одобрить и создать бота' }))
    await waitFor(() => expect(onDone).toHaveBeenCalled())
    expect(bodyOf(fetch, URL_)).toEqual({ coins: 30000 })
    const init = fetch.mock.calls[0][1] as RequestInit
    expect(new Headers(init.headers).get('X-Nox-CSRF')).toBe('tok123')
  })

  it('sends a preset, a changed slug and the key, then clears the key field', async () => {
    const fetch = mockFetch({ [URL_]: () => json({ ok: true }) })
    const user = userEvent.setup()
    setup()
    await user.click(screen.getByText('300 000'))
    const slug = screen.getByLabelText('Адрес бота')
    await user.clear(slug)
    await user.type(slug, 'primer-kz')
    const key = screen.getByLabelText('Ключ Anthropic клиента (необязательно)')
    expect(key).toHaveAttribute('type', 'password')
    await user.type(key, 'sk-ant-api03-' + 'A'.repeat(30))
    await user.click(screen.getByRole('button', { name: 'Одобрить и создать бота' }))
    await waitFor(() => expect(key).toHaveValue(''))
    expect(bodyOf(fetch, URL_)).toEqual({ coins: 300000, slug: 'primer-kz', anthropic_key: 'sk-ant-api03-' + 'A'.repeat(30) })
  })

  it('custom limit must be a whole number', async () => {
    const fetch = mockFetch({ [URL_]: () => json({ ok: true }) })
    const user = userEvent.setup()
    setup()
    await user.click(screen.getByText('Другой'))
    const submit = screen.getByRole('button', { name: 'Одобрить и создать бота' })
    expect(submit).toBeDisabled()
    const input = screen.getByLabelText('Свой лимит')
    await user.type(input, '12.5')
    expect(screen.getByText('Целое число')).toBeInTheDocument()
    expect(submit).toBeDisabled()
    await user.clear(input)
    await user.type(input, '50000')
    await user.click(submit)
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(bodyOf(fetch, URL_)).toEqual({ coins: 50000 })
  })

  it('rejects a malformed key and an invalid slug before sending', async () => {
    const fetch = mockFetch({ [URL_]: () => json({ ok: true }) })
    const user = userEvent.setup()
    setup()
    await user.type(screen.getByLabelText('Ключ Anthropic клиента (необязательно)'), 'not-a-key')
    expect(screen.getByText('Ключ должен начинаться с sk-ant-')).toBeInTheDocument()
    const slug = screen.getByLabelText('Адрес бота')
    await user.clear(slug)
    await user.type(slug, 'a_b')
    expect(screen.getByRole('button', { name: 'Одобрить и создать бота' })).toBeDisabled()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('hides the key field when the server cannot seal keys', () => {
    setup({ ...config, custom_key_supported: false })
    expect(screen.queryByLabelText('Ключ Anthropic клиента (необязательно)')).toBeNull()
  })

  it('shows a server error', async () => {
    mockFetch({ [URL_]: () => json({ error: { code: 'slug_taken', message: 'Этот адрес уже занят' } }, 409) })
    setup()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Одобрить и создать бота' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Этот адрес уже занят')
  })
})
