import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { BOT } from '../test/fixtures'
import { callsTo, jsonResponse, mockApi } from '../test/utils'
import type { BotSettings, NotifySettings, WhatsAppStatus } from '../api/types'
import { ReplyRulesFields, applySchedule, pauseLabel, scheduleOf } from './ReplyRules'
import { PhoneListInput, maskPhoneInput, parsePhoneList, phoneDigits } from './PhoneListInput'
import { BotPausedBanner, BotPauseSwitch, useBotPause } from './BotPause'
import { WhatsAppConnect } from './WhatsAppConnect'
import { LeadNotifications } from './LeadNotifications'
import { StatusBadge, untilLabel } from '../pages/Conversations/StatusBadge'

afterEach(() => vi.restoreAllMocks())

describe('phone list', () => {
  it('normalizes, masks and parses a pasted column', () => {
    expect(phoneDigits('8 (701) 123-45-67')).toBe('77011234567')
    expect(phoneDigits('701 123 45 67')).toBe('77011234567')
    expect(maskPhoneInput('87011234567')).toBe('+7 701 123 45 67')
    expect(maskPhoneInput('7701')).toBe('+7 701')
    const { phones, skipped } = parsePhoneList('+7 701 123 45 67\n87011234567\nАйгерим\n\t77020000001\n123')
    expect(phones).toEqual(['77011234567', '77020000001'])
    expect(skipped).toBe(2)
  })

  it('adds numbers typed with the mask and from a pasted column', async () => {
    const user = userEvent.setup()
    function Harness() {
      const [v, setV] = useState<string[]>([])
      return <PhoneListInput label="Не отвечать этим номерам" value={v} onChange={setV} />
    }
    render(<Harness />)
    const input = screen.getByLabelText('Не отвечать этим номерам', { selector: 'input' })
    await user.type(input, '87015550101')
    expect(input).toHaveValue('+7 701 555 01 01')
    await user.click(screen.getByRole('button', { name: 'Добавить' }))
    fireEvent.paste(input, { clipboardData: { getData: () => '+77015550102\n87015550103\nне номер' } })
    const list = screen.getByRole('list', { name: 'Не отвечать этим номерам' })
    expect(within(list).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['+7 701 555 01 01', '+7 701 555 01 02', '+7 701 555 01 03'])
    expect(screen.getByRole('status')).toHaveTextContent('не похожи на номер: 1')
    await user.click(screen.getByRole('button', { name: 'Убрать +7 701 555 01 02' }))
    expect(within(list).getAllByRole('listitem')).toHaveLength(2)
  })
})

describe('reply rules', () => {
  it('maps the schedule choice onto working-hours settings', () => {
    expect(scheduleOf(BOT)).toBe('always')
    const off = applySchedule(BOT, 'off_hours')
    expect(off).toMatchObject({ work_hours_mode: 'silent', after_hours_mode: 'reply' })
    expect(off.hours.always_open).toBe(false) // a schedule needs working hours
    expect(scheduleOf(off)).toBe('off_hours')
    expect(scheduleOf(applySchedule(BOT, 'work_hours'))).toBe('work_hours')
    expect(applySchedule(BOT, 'work_hours_message').after_hours_mode).toBe('fixed_message')
  })

  it('switches to new contacts only, explains it and shows the week editor for a schedule', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    function Harness() {
      const [v, setV] = useState<BotSettings>(BOT)
      return (
        <ReplyRulesFields
          value={v}
          knownCount={37}
          onChange={(n) => {
            onChange(n)
            setV(n)
          }}
        />
      )
    }
    render(<Harness />)
    expect(screen.getByText(/запомнено контактов/)).toHaveTextContent('37')
    await user.click(screen.getByRole('radio', { name: /Только новым контактам/ }))
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ new_contacts_only: true }))
    expect(screen.getByText(/Как бот понимает, кто новый/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Понедельник: открытие')).not.toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: /Только в нерабочее время/ }))
    expect(screen.getByLabelText('Понедельник: открытие')).toBeInTheDocument()
    expect(screen.getByLabelText('Часовой пояс')).toHaveValue('Asia/Almaty')
    await user.selectOptions(screen.getByLabelText('Бот молчит после ответа человека'), '720')
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ handover_pause_minutes: 720, work_hours_mode: 'silent' }))
  })
})

describe('handover pause labels', () => {
  it('uses the right Russian plural', () => {
    expect([60, 120, 240, 480, 720, 1440, 2880, 4320].map(pauseLabel)).toEqual([
      '1 час', '2 часа', '4 часа', '8 часов', '12 часов', 'сутки', '48 часов', '72 часа',
    ])
    expect(pauseLabel(90)).toBe('90 мин')
    expect(pauseLabel(21 * 60)).toBe('21 час')
  })
})

describe('bot pause', () => {
  function Harness() {
    const p = useBotPause()
    return (
      <>
        <BotPauseSwitch enabled={p.enabled} busy={p.busy} onToggle={() => void p.setPaused(p.enabled !== false)} />
        {p.enabled === false && <BotPausedBanner busy={p.busy} onResume={() => void p.setPaused(false)} />}
      </>
    )
  }

  it('pauses from the header and resumes from the banner', async () => {
    const user = userEvent.setup()
    let enabled = true
    const fetchMock = mockApi({
      'GET /api/settings/bot/pause': () => ({ enabled }),
      'POST /api/settings/bot/pause': (init: RequestInit) => {
        enabled = !JSON.parse(String(init.body)).paused
        return { enabled }
      },
    })
    render(<Harness />)
    const sw = await screen.findByRole('switch')
    expect(sw).toHaveAttribute('aria-checked', 'true')
    expect(sw).toHaveTextContent('Бот отвечает')
    await user.click(sw)
    await waitFor(() => expect(screen.getByRole('switch')).toHaveTextContent('Бот на паузе'))
    expect(screen.getByText(/клиентам никто не отвечает автоматически/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Включить бота' }))
    await waitFor(() => expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true'))
    const bodies = callsTo(fetchMock, 'POST', '/api/settings/bot/pause').map((c) => JSON.parse(String((c[1] as RequestInit).body)))
    expect(bodies).toEqual([{ paused: true }, { paused: false }])
  })
})

describe('number safety screen', () => {
  it('explains what happens to the number before showing the QR', async () => {
    const user = userEvent.setup()
    const fetchMock = mockApi({
      'GET /api/whatsapp/status': { status: 'close', phone: null, last_error: null } satisfies WhatsAppStatus,
      'POST /api/whatsapp/connect': { qr_base64: 'iVBORw0KGgo=' },
    })
    render(<WhatsAppConnect />)
    expect(await screen.findByRole('heading', { name: 'Что будет с вашим номером' })).toBeInTheDocument()
    expect(screen.getByText('WhatsApp на телефоне продолжит работать')).toBeInTheDocument()
    expect(screen.getByText('Бот пишет только тем, кто написал сам')).toBeInTheDocument()
    expect(screen.getByText('Выключить — одной кнопкой')).toBeInTheDocument()
    expect(screen.getByText(/Тестовом чате/)).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument() // no "I understand" box
    expect(callsTo(fetchMock, 'POST', '/api/whatsapp/connect')).toHaveLength(0)
    await user.click(screen.getByRole('button', { name: 'Показать QR-код' }))
    expect(await screen.findByRole('img', { name: /QR-код/ })).toHaveAttribute('src', 'data:image/png;base64,iVBORw0KGgo=')
    expect(callsTo(fetchMock, 'POST', '/api/whatsapp/connect')).toHaveLength(1)
  })

  it('offers the pairing code without a camera', async () => {
    const user = userEvent.setup()
    mockApi({ 'GET /api/whatsapp/status': { status: 'close', phone: null, last_error: null } })
    render(<WhatsAppConnect />)
    await user.click(await screen.findByRole('button', { name: /Подключить по коду/ }))
    expect(screen.getByLabelText('Рабочий номер WhatsApp')).toBeInTheDocument()
  })
})

describe('conversation status', () => {
  it('shows who leads the chat and until when', () => {
    const until = new Date()
    until.setHours(21, 30, 0, 0)
    render(<StatusBadge conversation={{ status: 'paused', paused_until: until.toISOString() }} />)
    expect(screen.getByText('Ведёт оператор до 21:30')).toBeInTheDocument()
    const tomorrow = new Date(until.getTime() + 24 * 3600_000)
    expect(untilLabel(tomorrow.toISOString(), until)).not.toBe('21:30')
  })
})

describe('lead notifications', () => {
  const NOTIFY: NotifySettings = {
    admin_phones: ['77015550000'],
    admin_group_jid: null,
    smtp: null,
    telegram_bot_token: null,
    telegram_chat_ids: [],
    admin_url: '',
  }

  it('finds the Telegram chat, saves without dropping other fields and sends a test', async () => {
    const user = userEvent.setup()
    let stored: NotifySettings = { ...NOTIFY, admin_group_jid: '120363000000000000@g.us' }
    const fetchMock = mockApi({
      'GET /api/settings/notify': () => stored,
      'GET /api/whatsapp/status': { status: 'open', phone: '77015551234', last_error: null },
      'PUT /api/settings/notify': (init: RequestInit) => {
        const body = JSON.parse(String(init.body)) as NotifySettings
        stored = { ...body, telegram_bot_token: body.telegram_bot_token ? '••••' : null }
        return stored
      },
      'POST /api/settings/notify/telegram/chats': { ok: true, chats: [{ id: '555', title: 'Айгерим' }] },
      'POST /api/settings/notify/test': { whatsapp: { ok: true, sent: 1 }, telegram: { ok: true, sent: 1 } },
    })
    render(<LeadNotifications />)
    await user.type(await screen.findByLabelText('Токен Telegram-бота'), '123456789:AAH-test_token_value_0123456789')
    await user.click(screen.getByRole('button', { name: 'Найти чаты' }))
    expect(await screen.findByRole('checkbox', { name: 'Айгерим' })).toBeChecked() // the only chat is picked
    const lookup = JSON.parse(String((callsTo(fetchMock, 'POST', '/api/settings/notify/telegram/chats')[0][1] as RequestInit).body))
    expect(lookup.token).toBe('123456789:AAH-test_token_value_0123456789')

    await user.click(screen.getByRole('button', { name: 'Отправить тестовое уведомление' }))
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/settings/notify/test')).toHaveLength(1))
    const put = JSON.parse(String((callsTo(fetchMock, 'PUT', '/api/settings/notify')[0][1] as RequestInit).body)) as NotifySettings
    expect(put).toMatchObject({
      admin_phones: ['77015550000'],
      admin_group_jid: '120363000000000000@g.us', // kept from the stored settings
      telegram_bot_token: '123456789:AAH-test_token_value_0123456789',
      telegram_chat_ids: ['555'],
    })
    expect(put.admin_url).toMatch(/^http/)
    expect(await screen.findByText(/WhatsApp: отправлено/)).toBeInTheDocument()
    expect(screen.getByText(/Telegram: отправлено/)).toBeInTheDocument()
  })

  it('warns when the admin number is the bot number itself', async () => {
    mockApi({
      'GET /api/settings/notify': { ...NOTIFY, admin_phones: ['77015551234'] },
      'GET /api/whatsapp/status': () => jsonResponse({ status: 'open', phone: '77015551234', last_error: null }),
    })
    render(<LeadNotifications />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Это номер самого бота')
  })
})
