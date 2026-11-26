import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { bodyOf, json, mockFetch, renderAt } from '../test/utils'
import { Login } from './Login'
import { Register, slugProblem } from './Register'
import { Verify } from './Verify'

async function fillRegister(user: ReturnType<typeof userEvent.setup>, over: Partial<Record<string, string>> = {}) {
  const v: Record<string, string> = {
    company: 'ТОО Пример',
    contact: 'Айгерим',
    email: 'Owner@Example.kz',
    phone: '+7 701 000 00 00',
    slug: 'primer',
    password: 'correct-horse-1',
    ...over,
  }
  await user.type(screen.getByLabelText('Название компании'), v.company)
  await user.type(screen.getByLabelText('Контактное лицо'), v.contact)
  await user.type(screen.getByLabelText('E-mail'), v.email)
  await user.type(screen.getByLabelText('Телефон'), v.phone)
  await user.type(screen.getByRole('textbox', { name: 'Адрес бота' }), v.slug)
  await user.type(screen.getByLabelText('Пароль'), v.password)
  await user.type(screen.getByLabelText('Повторите пароль'), v.password2 ?? v.password)
}

describe('Register', () => {
  it('slug rules match the backend', () => {
    expect(slugProblem('primer')).toBeNull()
    expect(slugProblem('ab')).not.toBeNull()
    expect(slugProblem('1abc')).not.toBeNull()
    expect(slugProblem('abc-')).not.toBeNull()
    expect(slugProblem('a--b')).not.toBeNull()
    expect(slugProblem('a'.repeat(33))).not.toBeNull()
  })

  it('submits the application and goes to the code page', async () => {
    const fetch = mockFetch({
      'GET /api/public-config': () => json({ instance_domain: 'example.kz' }),
      'GET /api/slug-check': () => json({ slug: 'primer', available: true, message: null }),
      'POST /api/auth/register': () => json({ ok: true }, 202),
    })
    const user = userEvent.setup()
    renderAt('/register', <Register />)
    expect(await screen.findByText('.example.kz')).toBeInTheDocument()
    await fillRegister(user)
    const submit = screen.getByRole('button', { name: 'Отправить заявку' })
    expect(submit).toBeDisabled() // consent not given yet
    await user.click(screen.getByRole('checkbox'))
    expect(submit).toBeEnabled()
    await user.click(submit)
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/verify'))
    expect(bodyOf(fetch, 'POST /api/auth/register')).toEqual({
      company: 'ТОО Пример',
      contact_name: 'Айгерим',
      email: 'owner@example.kz',
      phone: '+7 701 000 00 00',
      slug: 'primer',
      password: 'correct-horse-1',
      consent: true,
    })
    expect(sessionStorage.getItem('nox-pending-email')).toBe('owner@example.kz')
  })

  it('blocks short and mismatched passwords', async () => {
    mockFetch({ 'GET /api/public-config': () => json({ instance_domain: 'example.kz' }) })
    const user = userEvent.setup()
    renderAt('/register', <Register />)
    await fillRegister(user, { password: 'short', password2: 'other' })
    await user.click(screen.getByRole('checkbox'))
    expect(screen.getByText('Не короче 10 символов')).toBeInTheDocument()
    expect(screen.getByText('Пароли не совпадают')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Отправить заявку' })).toBeDisabled()
  })

  it('shows a taken slug next to the field', async () => {
    mockFetch({
      'GET /api/public-config': () => json({ instance_domain: 'example.kz' }),
      'GET /api/slug-check': () => json({ slug: 'primer', available: false, message: 'этот адрес уже занят' }),
      'POST /api/auth/register': () =>
        json({ error: { code: 'slug_taken', message: 'Этот адрес бота уже занят, выберите другой' } }, 409),
    })
    const user = userEvent.setup()
    renderAt('/register', <Register />)
    await fillRegister(user)
    await user.click(screen.getByRole('checkbox'))
    expect(await screen.findByText('этот адрес уже занят')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Отправить заявку' }))
    expect(await screen.findByText('Этот адрес бота уже занят, выберите другой')).toBeInTheDocument()
  })
})

describe('Verify', () => {
  it('sends the 6-digit code and opens the cabinet', async () => {
    const fetch = mockFetch({ 'POST /api/auth/verify': () => json({ status: 'verified' }) })
    const user = userEvent.setup()
    renderAt('/verify', <Verify />, { email: 'owner@example.kz' })
    expect(screen.getByLabelText('E-mail')).toHaveValue('owner@example.kz')
    const code = screen.getByLabelText('Код из письма')
    await user.type(code, '12a34567')
    expect(code).toHaveValue('123456')
    await user.click(screen.getByRole('button', { name: 'Подтвердить' }))
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/'))
    expect(bodyOf(fetch, 'POST /api/auth/verify')).toEqual({ email: 'owner@example.kz', code: '123456' })
  })

  it('shows the server error for a wrong code', async () => {
    mockFetch({
      'POST /api/auth/verify': () => json({ error: { code: 'invalid_code', message: 'Неверный код. Осталось попыток: 4.' } }, 400),
    })
    const user = userEvent.setup()
    renderAt('/verify', <Verify />, { email: 'owner@example.kz' })
    await user.type(screen.getByLabelText('Код из письма'), '000000')
    await user.click(screen.getByRole('button', { name: 'Подтвердить' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Осталось попыток: 4')
  })

  it('resend waits for the cooldown after registration', async () => {
    renderAt('/verify', <Verify />, { email: 'owner@example.kz' })
    expect(screen.getByRole('button', { name: /Отправить код повторно через \d+ с/ })).toBeDisabled()
  })

  it('resend is available when the page is opened directly', async () => {
    const fetch = mockFetch({ 'POST /api/auth/resend': () => json({ ok: true }) })
    sessionStorage.setItem('nox-pending-email', 'owner@example.kz')
    const user = userEvent.setup()
    renderAt('/verify', <Verify />)
    await user.click(screen.getByRole('button', { name: 'Отправить код повторно' }))
    expect(bodyOf(fetch, 'POST /api/auth/resend')).toEqual({ email: 'owner@example.kz' })
    expect(await screen.findByRole('status')).toHaveTextContent('новый код')
  })
})

describe('Login', () => {
  it('logs in and opens the cabinet', async () => {
    const fetch = mockFetch({ 'POST /api/auth/login': () => json({ status: 'active' }) })
    const user = userEvent.setup()
    renderAt('/login', <Login />)
    await user.type(screen.getByLabelText('E-mail'), ' Owner@Example.kz ')
    await user.type(screen.getByLabelText('Пароль'), 'correct-horse-1')
    await user.click(screen.getByRole('button', { name: 'Войти' }))
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/'))
    expect(bodyOf(fetch, 'POST /api/auth/login')).toEqual({ email: 'owner@example.kz', password: 'correct-horse-1' })
  })

  it('shows wrong credentials', async () => {
    mockFetch({
      'POST /api/auth/login': () => json({ error: { code: 'invalid_credentials', message: 'x' } }, 401),
    })
    const user = userEvent.setup()
    renderAt('/login', <Login />)
    await user.type(screen.getByLabelText('E-mail'), 'a@example.kz')
    await user.type(screen.getByLabelText('Пароль'), 'wrong-password')
    await user.click(screen.getByRole('button', { name: 'Войти' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Неверный e-mail или пароль.')
  })

  it('sends an unverified account to the code page', async () => {
    mockFetch({
      'POST /api/auth/login': () => json({ error: { code: 'email_not_verified', message: 'Подтвердите e-mail' } }, 403),
    })
    const user = userEvent.setup()
    renderAt('/login', <Login />)
    await user.type(screen.getByLabelText('E-mail'), 'a@example.kz')
    await user.type(screen.getByLabelText('Пароль'), 'correct-horse-1')
    await user.click(screen.getByRole('button', { name: 'Войти' }))
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/verify'))
    expect(sessionStorage.getItem('nox-pending-email')).toBe('a@example.kz')
  })
})
