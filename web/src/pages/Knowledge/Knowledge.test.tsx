import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { Knowledge } from './Knowledge'
import { callsTo, mockApi } from '../../test/utils'
import { BOT } from '../../test/fixtures'
import type { BotSettings } from '../../api/types'
import { normalizePrice, priceMissing } from '../../components/ServicesEditor'

function renderPage(bot: BotSettings, extra: Record<string, unknown> = {}) {
  let saved: BotSettings | null = null
  const fetchMock = mockApi({
    'GET /api/settings/bot': () => saved ?? bot,
    'PUT /api/settings/bot': (init: RequestInit) => {
      saved = JSON.parse(String(init.body)) as BotSettings
      return saved
    },
    'GET /api/knowledge/import-prices': { photo: true, provider: 'anthropic' },
    ...extra,
  })
  render(
    <MemoryRouter>
      <Knowledge />
    </MemoryRouter>,
  )
  return { fetchMock, saved: () => saved }
}

describe('Услуги и мастера', () => {
  it('imports pasted prices with a preview, drops a row and saves the list', async () => {
    const user = userEvent.setup()
    const { fetchMock, saved } = renderPage(
      { ...BOT, template_id: 'beauty_salon', services: [{ name: 'Маникюр классический', price: 'от … ₸', duration_min: 60 }] },
      {
        'POST /api/knowledge/import-prices': {
          source: 'text',
          rows: [
            { name: 'Маникюр классический', price: '5 000 ₸', duration_min: 60 },
            { name: 'Телефон 8 777 123', price: '123 ₸', duration_min: null },
            { name: 'Педикюр', price: 'от 7 000 ₸', duration_min: 90 },
          ],
          warnings: ['Пропущено строк без цены: 1 (обычно это заголовки разделов).'],
          coins: null,
        },
      },
    )
    expect(await screen.findByText(/Не заполнено цен: 1/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Загрузить прайс' }))
    await user.click(screen.getByRole('radio', { name: 'Вставить текст' }))
    await user.type(screen.getByLabelText(/По одной услуге на строку/), 'Маникюр — 5000')
    await user.click(screen.getByRole('button', { name: 'Разобрать' }))

    const preview = await screen.findByRole('list', { name: 'Найденные услуги' })
    expect(screen.getByText(/Пропущено строк без цены/)).toBeInTheDocument()
    await user.click(within(preview).getByRole('button', { name: 'Удалить «Телефон 8 777 123»' }))
    expect(within(preview).getAllByRole('listitem')).toHaveLength(2)
    expect(JSON.parse(String((callsTo(fetchMock, 'POST', '/api/knowledge/import-prices')[0][1] as RequestInit).body))).toEqual({ text: 'Маникюр — 5000' })

    await user.click(screen.getByRole('button', { name: 'Добавить к списку' }))
    expect(screen.queryByRole('list', { name: 'Найденные услуги' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Не заполнено цен/)).not.toBeInTheDocument() // the import filled the placeholder price

    await user.click(screen.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(saved()).not.toBeNull())
    expect(saved()!.services).toEqual([
      { name: 'Маникюр классический', price: '5 000 ₸', duration_min: 60 },
      { name: 'Педикюр', price: 'от 7 000 ₸', duration_min: 90 },
    ])
    expect(saved()!.prompt).toBe(BOT.prompt) // the rest of the settings is kept
  })

  it('shows an import error from the server', async () => {
    const user = userEvent.setup()
    renderPage(BOT, {
      'POST /api/knowledge/import-prices': () =>
        new Response(JSON.stringify({ error: { code: 'price_import_failed', message: 'Старый формат Excel (.xls) не поддерживается' } }), {
          status: 422,
          headers: { 'Content-Type': 'application/json' },
        }),
    })
    await user.click(await screen.findByRole('button', { name: 'Загрузить прайс' }))
    await user.click(screen.getByRole('radio', { name: 'Вставить текст' }))
    await user.type(screen.getByLabelText(/По одной услуге на строку/), 'x')
    await user.click(screen.getByRole('button', { name: 'Разобрать' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Старый формат Excel')
  })

  it('adds a master with working days, services and the no-online flag', async () => {
    const user = userEvent.setup()
    const { saved } = renderPage({ ...BOT, services: [{ name: 'Маникюр', price: '5 000 ₸', duration_min: 60 }] })
    await user.click(await screen.findByRole('button', { name: 'Добавить специалиста' }))
    await user.type(screen.getByLabelText('Имя'), 'Айгерим')
    const days = screen.getByRole('group', { name: 'Айгерим: дни работы' })
    await user.click(within(days).getByRole('button', { name: 'Пн' })) // Mon off
    await user.click(within(days).getByRole('button', { name: 'Сб' })) // Sat on
    await user.click(within(screen.getByRole('group', { name: 'Айгерим: услуги' })).getByRole('button', { name: 'Маникюр' }))
    await user.click(screen.getByLabelText(/Не записывать онлайн/))
    await user.click(screen.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(saved()).not.toBeNull())
    const m = saved()!.staff[0]
    expect(m).toMatchObject({ name: 'Айгерим', services: ['Маникюр'], no_online_booking: true })
    expect(m.mon.works).toBe(false)
    expect(m.sat).toEqual({ works: true, open: '10:00', close: '19:00' })
    expect(m.thu.works).toBe(true)
  })

  it('normalizes typed prices and flags placeholders', () => {
    expect(normalizePrice('5000')).toBe('5 000 ₸')
    expect(normalizePrice('от 12000 тг')).toBe('от 12 000 ₸')
    expect(normalizePrice('по договорённости')).toBe('по договорённости')
    expect(priceMissing('от … ₸')).toBe(true)
    expect(priceMissing('')).toBe(true)
    expect(priceMissing('4 000 ₸')).toBe(false)
  })
})
