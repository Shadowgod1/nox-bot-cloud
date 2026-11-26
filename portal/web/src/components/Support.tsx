import { useEffect, useState } from 'react'
import { Mail, MessageCircle, Phone } from 'lucide-react'
import { loadPublicConfig, type PublicConfig, type SupportContacts } from '../api'
import { cx } from './ui'

/** /api/public-config (fetched once per page load); null until it arrives or when it failed. */
export function usePublicConfig(): PublicConfig | null {
  const [config, setConfig] = useState<PublicConfig | null>(null)
  useEffect(() => {
    let alive = true
    loadPublicConfig()
      .then((c) => alive && setConfig(c))
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [])
  return config
}

export function hasPhoneOrWhatsapp(s: SupportContacts | undefined | null): s is SupportContacts {
  return !!s && !!(s.phone || s.whatsapp_url)
}

export function telHref(phone: string): string {
  const digits = phone.replace(/\D/g, '')
  return `tel:${phone.trim().startsWith('+') ? '+' : ''}${digits}`
}

/** "77084707839" / "8 708 470 7839" → "+7 708 470 78 39"; anything else is shown as typed. */
export function formatPhone(phone: string): string {
  let d = phone.replace(/\D/g, '')
  if (d.length === 11 && d.startsWith('8')) d = '7' + d.slice(1)
  if (d.length !== 11 || !d.startsWith('7')) return phone.trim()
  return `+7 ${d.slice(1, 4)} ${d.slice(4, 7)} ${d.slice(7, 9)} ${d.slice(9)}`
}

interface LinksProps {
  support: SupportContacts | undefined | null
  /** also the e-mail (always set) */
  email?: boolean
  className?: string
  linkClassName?: string
}

/** Phone (tel:), WhatsApp (wa.me, new tab) and optionally e-mail; the ones not configured are not shown. */
export function SupportLinks({ support, email = false, className, linkClassName }: LinksProps) {
  if (!support) return null
  const link = cx('inline-flex items-center gap-1.5 font-semibold text-ink-2 hover:text-accent', linkClassName)
  const items = [
    support.phone && (
      <a key="phone" href={telHref(support.phone)} className={link}>
        <Phone className="size-4 flex-none" aria-hidden />
        <span className="whitespace-nowrap">{formatPhone(support.phone)}</span>
      </a>
    ),
    support.whatsapp_url && (
      <a key="wa" href={support.whatsapp_url} target="_blank" rel="noopener noreferrer" className={link}>
        <MessageCircle className="size-4 flex-none" aria-hidden />
        WhatsApp
      </a>
    ),
    email && support.email && (
      <a key="mail" href={`mailto:${support.email}`} className={link}>
        <Mail className="size-4 flex-none" aria-hidden />
        {support.email}
      </a>
    ),
  ].filter(Boolean)
  if (!items.length) return null
  return <span className={cx('flex flex-wrap items-center gap-x-4 gap-y-2', className)}>{items}</span>
}
