import { describe, it, expect } from 'vitest'
import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SecretInput, secretPayload, secretState } from './SecretInput'
import type { SecretValue } from '../api/types'

// The settings forms that hold secrets (e.g. the SMTP password) share SecretInput.
function Harness({ original }: { original: SecretValue }) {
  const [value, setValue] = useState(() => secretState(original))
  return (
    <>
      <SecretInput label="Ключ приложения" value={value} onChange={setValue} />
      <output data-testid="payload">{JSON.stringify(secretPayload(value))}</output>
    </>
  )
}

const payload = () => JSON.parse(screen.getByTestId('payload').textContent ?? 'undefined')

describe('SecretInput', () => {
  it('keeps the masked key untouched', () => {
    render(<Harness original="••••abcd" />)
    expect(screen.getByText('••••abcd')).toBeInTheDocument()
    expect(payload()).toBe('••••abcd')
  })

  it('sends the new key only when replaced', async () => {
    render(<Harness original="••••abcd" />)
    await userEvent.click(screen.getByRole('button', { name: 'Заменить ключ приложения' }))
    await userEvent.type(screen.getByLabelText('Ключ приложения'), 'new-key-9999')
    expect(payload()).toBe('new-key-9999')
  })

  it('sends null only when the user removes the key', async () => {
    render(<Harness original="••••abcd" />)
    await userEvent.click(screen.getByRole('button', { name: 'Удалить ключ приложения' }))
    expect(payload()).toBeNull()
  })
})
