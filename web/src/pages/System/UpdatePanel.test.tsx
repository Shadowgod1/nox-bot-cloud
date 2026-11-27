import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { UpdatePanel } from './System'
import { mockApi } from '../../test/utils'

const NO_JOB = { job_id: null, state: null, log: '' }

describe('UpdatePanel', () => {
  it('up to date: no version offered, the button is disabled', async () => {
    mockApi({
      'GET /api/system/version': { current: '0.4.9', available: null, core_version: '0.4.9', updater_available: true, managed: false },
      'GET /api/system/update/status': NO_JOB,
    })
    render(<UpdatePanel />)
    expect(await screen.findByText('Последняя версия')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Обновить' })).toBeDisabled()
  })

  it('cloud install: Nox updates it, no button and no "available" column', async () => {
    mockApi({
      'GET /api/system/version': { current: '0.4.9', available: null, core_version: '0.4.9', updater_available: false, managed: true },
      'GET /api/system/update/status': NO_JOB,
    })
    render(<UpdatePanel />)
    expect(await screen.findByText('0.4.9')).toBeInTheDocument()
    expect(screen.getByText(/Обновления устанавливает Nox service/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Обновить' })).toBeNull()
    expect(screen.queryByText('Доступна')).toBeNull()
    expect(screen.queryByText(/Сервис обновлений недоступен/)).toBeNull()
  })
})
