import { describe, expect, it } from 'vitest'
import type { Booking } from '../../api/types'
import { bookingDetails } from './Bookings'

const base = { id: 1, conversation_id: 1, status: 'pending', created_at: '2026-11-27T10:00:00Z', phone: '', name: '' } as unknown as Booking

describe('bookingDetails', () => {
  it('a salon request shows the service and the specialist, no seats or hours', () => {
    const text = bookingDetails({ ...base, payload: { date: '2026-11-28', time: '15:00', service: 'Маникюр', specialist: 'Айгерим' } })
    expect(text).toContain('15:00 · Маникюр · Айгерим')
    expect(text).not.toMatch(/мест|ч\b/)
  })

  it('a club request still shows hours, seats and the zone', () => {
    const text = bookingDetails({ ...base, payload: { date: '2026-11-12', time: '19:00', duration_hours: 3, seats: 5, zone: 'VIP' } })
    expect(text).toContain('19:00 · 3 ч · 5 мест · VIP')
  })
})
