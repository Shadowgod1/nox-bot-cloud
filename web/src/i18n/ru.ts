// Shared Russian UI copy. Page-specific prose lives next to the page.
import type {
  BookingStatus,
  ConversationStatus,
  MessageAuthor,
  UpdateState,
  Weekday,
  WhatsAppState,
} from '../api/types'
import { brandCopy } from '../brand'

export const ru = {
  nav: {
    dashboard: 'Обзор',
    conversations: 'Диалоги',
    bookings: 'Заявки',
    bot: 'Настройки бота',
    knowledge: 'Услуги и мастера',
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
    // getter: the brand is known only after GET /api/brand answers
    get network() {
      return brandCopy().id === 'nox'
        ? 'Нет связи с сервером. Проверьте подключение к интернету.'
        : 'Нет связи с сервером. Проверьте, что ILER запущен.'
    },
    server: 'Ошибка сервера. Попробуйте ещё раз через минуту.',
    validation: 'Проверьте заполнение полей.',
    forbidden: 'Недостаточно прав для этого действия.',
    rateLimited: 'Слишком много попыток. Подождите немного.',
    generic: 'Что-то пошло не так.',
  },
  /**
   * Russian text for error codes core returns in `{error:{code,message}}` (core messages are English).
   * Codes in `errorDetail` also get the server message appended (it carries the useful specifics).
   */
  errorCodes: {
    invalid_credentials: 'Неверный e-mail или пароль.',
    locked: 'Слишком много неудачных попыток входа. Попробуйте позже.',
    unauthorized: 'Требуется вход.',
    forbidden: 'Недостаточно прав для этого действия.',
    csrf_failed: 'Сессия устарела. Обновите страницу.',
    not_found: 'Не найдено.',
    validation_error: 'Проверьте заполнение полей.',
    already_set_up: 'Первичная настройка уже выполнена. Войдите в систему.',
    email_taken: 'Пользователь с таким e-mail уже есть.',
    cannot_delete_self: 'Нельзя удалить собственную учётную запись.',
    last_owner: 'Нельзя удалить последнего владельца.',
    already_decided: 'По заявке уже принято решение.',
    already_connected: 'WhatsApp уже подключён.',
    wrong_state: 'Код привязки можно получить только при отключённом WhatsApp.',
    evolution_unavailable: 'Сервис WhatsApp недоступен.',
    send_failed: 'Сообщение не отправлено.',
    llm_error: 'Ошибка языковой модели.',
    updater_unavailable: 'Сервис обновлений недоступен.',
    updater_error: 'Ошибка сервиса обновлений.',
    update_in_progress: 'Обновление уже выполняется.',
    backup_in_progress: 'Уже выполняется резервное копирование или восстановление.',
    backup_failed: 'Не удалось создать резервную копию.',
    restore_failed: 'Не удалось восстановить данные.',
    invalid_backup_name: 'Некорректное имя резервной копии.',
    template_invalid: 'Шаблон повреждён.',
    setup_token_required: 'Откройте ссылку из установщика (она содержит одноразовый токен).',
    setup_token_invalid: 'Откройте ссылку из установщика (она содержит одноразовый токен).',
    not_implemented: 'Функция пока не реализована.',
  } as Record<string, string>,
  errorDetail: ['validation_error', 'evolution_unavailable', 'send_failed', 'llm_error', 'backup_failed', 'restore_failed', 'template_invalid'],
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
