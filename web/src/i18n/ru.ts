// Shared Russian UI copy. Page-specific prose lives next to the page.
import type {
  BookingStatus,
  ConversationStatus,
  MessageAuthor,
  UpdateState,
  Weekday,
  WhatsAppState,
} from '../api/types'

export const ru = {
  brand: 'ILER',
  nav: {
    dashboard: 'Обзор',
    conversations: 'Диалоги',
    bookings: 'Заявки',
    bot: 'Настройки бота',
    testChat: 'Тестовый чат',
    integrations: 'Интеграции',
    system: 'Система',
  },
  common: {
    save: 'Сохранить',
    saving: 'Сохранение…',
    saved: 'Сохранено',
    cancel: 'Отмена',
    retry: 'Повторить',
    loading: 'Загрузка…',
    add: 'Добавить',
    remove: 'Удалить',
    check: 'Проверить',
    checking: 'Проверка…',
    back: 'Назад',
    next: 'Далее',
    logout: 'Выйти',
    unsaved: 'Есть несохранённые изменения',
    discard: 'Отменить изменения',
    loadError: 'Не удалось загрузить данные.',
  },
  errors: {
    network: 'Нет связи с сервером. Проверьте, что ILER запущен.',
    server: 'Ошибка сервера. Попробуйте ещё раз через минуту.',
    validation: 'Проверьте заполнение полей.',
    forbidden: 'Недостаточно прав для этого действия.',
    rateLimited: 'Слишком много попыток. Подождите немного.',
    generic: 'Что-то пошло не так.',
  },
  waStatus: {
    open: 'Подключён',
    connecting: 'Подключается',
    close: 'Не подключён',
    degraded: 'Сбой отправки',
  } satisfies Record<WhatsAppState, string>,
  convStatus: {
    active: 'Бот отвечает',
    paused: 'Оператор',
    needs_operator: 'Нужен оператор',
    closed: 'Закрыт',
  } satisfies Record<ConversationStatus, string>,
  author: {
    client: 'Клиент',
    bot: 'Бот',
    operator: 'Оператор',
    system: 'Система',
  } satisfies Record<MessageAuthor, string>,
  bookingStatus: {
    pending: 'Ожидает',
    confirmed: 'Подтверждена',
    rejected: 'Отклонена',
    expired: 'Истекла',
  } satisfies Record<BookingStatus, string>,
  updateState: {
    idle: 'Нет активного обновления',
    queued: 'В очереди',
    backup: 'Создаётся резервная копия',
    pulling: 'Загрузка новой версии',
    restarting: 'Перезапуск сервисов',
    healthy: 'Обновление завершено',
    rolled_back: 'Обновление не удалось, выполнен откат',
    failed: 'Обновление не удалось',
  } satisfies Record<UpdateState, string>,
  weekdays: {
    mon: 'Понедельник',
    tue: 'Вторник',
    wed: 'Среда',
    thu: 'Четверг',
    fri: 'Пятница',
    sat: 'Суббота',
    sun: 'Воскресенье',
  } satisfies Record<Weekday, string>,
  roles: { owner: 'Владелец', operator: 'Оператор' },
}

const timeFmt = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' })
const dateFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' })
const fullFmt = new Intl.DateTimeFormat('ru-RU', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
})

function sameDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
}

/** "14:05" today, "вчера" yesterday, "3 нояб." otherwise. */
export function formatShort(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  if (sameDay(d, now)) return timeFmt.format(d)
  const y = new Date(now)
  y.setDate(now.getDate() - 1)
  if (sameDay(d, y)) return 'вчера'
  return dateFmt.format(d)
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : timeFmt.format(d)
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : fullFmt.format(d)
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} Б`
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} КБ`
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} МБ`
  return `${(n / 1024 ** 3).toFixed(2)} ГБ`
}

/** Russian plural: plural(5, ['диалог', 'диалога', 'диалогов']). */
export function plural(n: number, forms: [string, string, string]): string {
  const a = Math.abs(n) % 100
  const b = a % 10
  if (a > 10 && a < 20) return forms[2]
  if (b > 1 && b < 5) return forms[1]
  if (b === 1) return forms[0]
  return forms[2]
}
