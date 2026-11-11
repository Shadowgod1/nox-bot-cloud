import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BotSettingsPage } from './BotSettings'
import { BOT } from '../../test/fixtures'
import { callsTo, mockApi } from '../../test/utils'
import type { BotSettings, TemplateMeta } from '../../api/types'

const TEMPLATES: TemplateMeta[] = [
  {
    id: 'clinic',
    name: 'Клиника / стоматология',
    description: 'Запись к врачу',
    fields: [
      { key: 'business_name', label: 'Название клиники', type: 'text', required: true, placeholder: '', hint: '' },
      { key: 'services', label: 'Услуги и цены', type: 'textarea', required: true, placeholder: '', hint: '' },
    ],
    defaults: {},
  },
]

function setup(bot: BotSettings) {
  let saved: BotSettings | null = null
  const fetchMock = mockApi({
    'GET /api/settings/bot': bot,
    'GET /api/templates': TEMPLATES,
    'POST /api/templates/clinic/render': { prompt: 'Новый промпт из шаблона', missing: [] },
    'PUT /api/settings/bot': (init: RequestInit) => {
      saved = JSON.parse(String(init.body)) as BotSettings
      return saved
    },
  })
  render(<BotSettingsPage />)
  return { fetchMock, saved: () => saved }
}

describe('BotSettingsPage', () => {
  afterEach(() => vi.restoreAllMocks())

  const edited: BotSettings = {
    ...BOT,
    template_id: 'clinic',
    template_values: { business_name: 'Улыбка', services: 'Чистка — 15000 ₸', stale_key: 'x' },
    prompt: 'Промпт с ручными правками',
    prompt_edited: true,
  }

  it('asks before overwriting a manually edited prompt and keeps it on cancel', async () => {
    const user = userEvent.setup()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const { fetchMock } = setup(edited)
    expect(await screen.findByLabelText('Текст промпта')).toHaveValue('Промпт с ручными правками')
    expect(screen.getByText('Изменён вручную')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Собрать промпт из шаблона' }))
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('Ручные правки будут потеряны'))
    expect(callsTo(fetchMock, 'POST', '/api/templates/clinic/render')).toHaveLength(0)
    expect(screen.getByLabelText('Текст промпта')).toHaveValue('Промпт с ручными правками')
  })

  it('regenerates on confirm, sends only declared keys and saves the result', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { fetchMock, saved } = setup(edited)
    await user.click(await screen.findByRole('button', { name: 'Собрать промпт из шаблона' }))
    await waitFor(() => expect(screen.getByLabelText('Текст промпта')).toHaveValue('Новый промпт из шаблона'))
    const body = JSON.parse(String((callsTo(fetchMock, 'POST', '/api/templates/clinic/render')[0][1] as RequestInit).body))
    expect(body).toEqual({ values: { business_name: 'Улыбка', services: 'Чистка — 15000 ₸' } })
    await user.click(screen.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(saved()).not.toBeNull())
    expect(saved()).toMatchObject({ prompt: 'Новый промпт из шаблона', prompt_edited: false, template_id: 'clinic' })
  })

  it('marks the prompt as edited on manual change, no confirm when not edited, behaviour is collapsed', async () => {
    const user = userEvent.setup()
    const confirm = vi.spyOn(window, 'confirm')
    setup({ ...edited, prompt_edited: false })
    await user.click(await screen.findByRole('button', { name: 'Собрать промпт из шаблона' }))
    await waitFor(() => expect(screen.getByLabelText('Текст промпта')).toHaveValue('Новый промпт из шаблона'))
    expect(confirm).not.toHaveBeenCalled()
    await user.type(screen.getByLabelText('Текст промпта'), '!')
    expect(screen.getByText('Изменён вручную')).toBeInTheDocument()

    const toggle = screen.getByRole('button', { name: /Поведение/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByLabelText('Имя бота')).not.toBeInTheDocument()
    await user.click(toggle)
    expect(screen.getByLabelText('Имя бота')).toBeVisible()
  })

  it('warns that the bot is silent while the prompt is empty', async () => {
    setup(BOT)
    expect(await screen.findByText(/Бот не отвечает клиентам, пока промпт пуст/)).toBeInTheDocument()
  })
})
