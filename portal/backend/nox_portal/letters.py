"""Russian e-mail templates (subject, text). The signature is added by the mailer."""

from nox_portal.config import get_settings


def _portal() -> str:
    return get_settings().public_url.rstrip("/")


def verify_code(name: str, code: str) -> tuple[str, str]:
    return (
        f"Код подтверждения Nox Bot: {code}",
        f"Здравствуйте, {name}!\n\n"
        f"Ваш код подтверждения e-mail: {code}\n\n"
        "Код действует 15 минут. Если вы не регистрировались в Nox Bot, просто проигнорируйте это письмо.",
    )


def reset_code(name: str, code: str) -> tuple[str, str]:
    return (
        f"Сброс пароля Nox Bot: {code}",
        f"Здравствуйте, {name}!\n\n"
        f"Код для сброса пароля: {code}\n\n"
        "Код действует 15 минут. Если вы не запрашивали сброс, проигнорируйте письмо — пароль не изменится.",
    )


def already_registered(name: str) -> tuple[str, str]:
    return (
        "Повторная регистрация в Nox Bot",
        f"Здравствуйте, {name}!\n\n"
        "Кто-то попытался зарегистрироваться в Nox Bot с вашим адресом e-mail. Аккаунт с этим адресом "
        f"уже существует. Войти: {_portal()}/login\n\n"
        f"Если забыли пароль, восстановите его: {_portal()}/reset",
    )


def _comment(comment: str) -> str:
    return f"Комментарий менеджера: {comment}\n\n" if comment.strip() else ""


def staff_invoice_requested(company: str, tariff_name: str, amount: str, account_id: int) -> tuple[str, str]:
    return (
        f"Клиент выбрал оплату по счёту: {company}",
        f"Клиент выбрал тариф «{tariff_name}» ({amount} в месяц) и оплату по счёту через менеджера.\n\n"
        f"Выставите счёт в карточке заявки: {_portal()}/staff/applications/{account_id}",
    )


def staff_upgrade_requested(company: str, tariff_name: str, amount: str, account_id: int) -> tuple[str, str]:
    return (
        f"Переход с Free на платный тариф: {company}",
        f"Клиент на тестовом тарифе хочет перейти на «{tariff_name}» ({amount} в месяц) и оплатить по счёту.\n\n"
        f"Выставите счёт в карточке заявки: {_portal()}/staff/applications/{account_id}",
    )


def staff_topup_requested(company: str, account_id: int) -> tuple[str, str]:
    return (
        f"Запрос на пополнение коинов: {company}",
        "Клиент просит выставить счёт на пакет коинов.\n\n"
        f"Выставите счёт в карточке заявки: {_portal()}/staff/applications/{account_id}",
    )


def staff_renewal_due(company: str, period_end, tariff_name: str, amount: str, account_id: int) -> tuple[str, str]:
    return (
        f"Пора выставить счёт на продление: {company}",
        f"Оплаченный месяц заканчивается {_date(period_end)} (тариф «{tariff_name}», {amount}).\n\n"
        f"Выставите счёт на продление в карточке: {_portal()}/staff/applications/{account_id}",
    )


def renewal_reminder(name: str, period_end, grace_days: int) -> tuple[str, str]:
    return (
        "Nox Bot: скоро продление",
        f"Здравствуйте, {name}!\n\n"
        f"Оплаченный месяц обслуживания Nox Bot заканчивается {_date(period_end)}. Менеджер пришлёт счёт на "
        f"продление в ближайшие дни, он появится и в личном кабинете: {_portal()}/cabinet\n\n"
        f"Если оплата не поступит в течение {grace_days} дней после окончания месяца, работа бота будет "
        "приостановлена (данные сохранятся).",
    )


def staff_free_started(company: str, email: str, slug: str) -> tuple[str, str]:
    return (
        f"Подключён тест (Free): {company}",
        f"Клиент выбрал тариф Free (тест), бот создаётся автоматически.\n\nE-mail: {email}\n"
        f"Адрес: {slug}.{get_settings().instance_domain}\n\n{_portal()}/staff",
    )


def trial_ended(name: str) -> tuple[str, str]:
    return (
        "Nox Bot: тестовый период закончился",
        f"Здравствуйте, {name}!\n\n"
        "Тестовый период закончился, работа бота приостановлена. Настройки и данные сохранены.\n\n"
        f"Чтобы продолжить с тем же ботом, выберите платный тариф в личном кабинете: {_portal()}/cabinet",
    )


def trial_ending(name: str, free_until, days: int) -> tuple[str, str]:
    """Free trial reminder: days = 3 (three days left) or 1 (a day or less left)."""
    from zoneinfo import ZoneInfo

    when = free_until.astimezone(ZoneInfo(get_settings().tz)).strftime("%d.%m.%Y в %H:%M")
    left = "Остались последние сутки" if days <= 1 else f"Осталось {days} дня"
    return (
        f"Nox Bot: тест закончится {_date(free_until)}",
        f"Здравствуйте, {name}!\n\n"
        f"{left} бесплатного теста: он закончится {when} по времени Алматы.\n\n"
        "После этого бот перестанет отвечать клиентам. Настройки, услуги и переписка сохранятся — после оплаты "
        "тарифа бот продолжит с того же места, заново ничего настраивать не нужно.\n\n"
        f"Выбрать тариф: {_portal()}/cabinet\n"
        "Для ТОО и ИП есть оплата по счёту: выберите её в кабинете, и менеджер пришлёт счёт.",
    )


def staff_setup_help(company: str, contact: str, phone: str, email: str, account_id: int) -> tuple[str, str]:
    return (
        f"Просят помочь с настройкой: {company}",
        "Клиент нажал «Попросить помочь с настройкой»: нужно подключить WhatsApp и заполнить услуги вместе с ним.\n\n"
        f"Контакт: {contact}\nТелефон: {phone}\nE-mail: {email}\n\n"
        f"Когда свяжетесь, отметьте это в карточке: {_portal()}/staff/applications/{account_id}",
    )


def staff_trial_ended(company: str) -> tuple[str, str]:
    return (
        f"Тест закончился: {company}",
        f"Тестовый период (Free) закончился, аккаунт приостановлен до оплаты платного тарифа.\n\n{_portal()}/staff",
    )


def upgraded(name: str, tariff_name: str) -> tuple[str, str]:
    return (
        "Nox Bot: тариф подключён",
        f"Здравствуйте, {name}!\n\n"
        f"Оплата получена, подключён тариф «{tariff_name}». Бот продолжает работу, новый пакет коинов уже на "
        f"балансе.\n\nЛичный кабинет: {_portal()}/cabinet",
    )


def staff_online_paid(company: str, what: str, amount: str, provider: str) -> tuple[str, str]:
    return (
        f"Онлайн-оплата: {company}, {amount}",
        f"{what}: получена онлайн-оплата {amount} ({provider}). Платёж записан в журнал, действие выполнено "
        f"автоматически.\n\n{_portal()}/staff",
    )


def staff_online_problem(company: str, what: str, amount: str, provider: str, reason: str) -> tuple[str, str]:
    return (
        f"Онлайн-оплата требует внимания: {company}",
        f"{what}, {amount} ({provider}): {reason}.\n\n{_portal()}/staff",
    )


def staff_new_application(company: str, contact: str, email: str, phone: str, slug: str) -> tuple[str, str]:
    domain = get_settings().instance_domain
    return (
        f"Новая заявка Nox Bot: {company}",
        "Подтверждена новая заявка на бота.\n\n"
        f"Компания: {company}\nКонтакт: {contact}\nE-mail: {email}\nТелефон: {phone}\n"
        f"Адрес: {slug}.{domain}\n\n"
        f"Рассмотреть: {_portal()}/staff",
    )


def invoice(name: str, number: str, tariff_name: str, amount: str, due_days: int, comment: str = "") -> tuple[str, str]:
    s = get_settings()
    requisites = "\n".join(
        f"{label}: {value}" for label, value in (
            ("Получатель", s.seller_name), ("БИН", s.seller_bin), ("Банк", s.seller_bank), ("ИИК", s.seller_iik),
            ("БИК", s.seller_bik), ("Кбе", s.seller_kbe),
        ) if value
    )
    return (
        f"Счёт {number} на оплату Nox Bot",
        f"Здравствуйте, {name}!\n\n"
        f"Выставлен счёт {number} за тариф «{tariff_name}» на первый месяц: {amount}.\n"
        f"Оплатите его в течение {due_days} рабочих дней банковским переводом, в назначении платежа укажите "
        f"номер счёта {number}.\n\n"
        f"{requisites}\n\n"
        f"{_comment(comment)}"
        f"Счёт для печати (PDF): {_portal()}/invoice\n\n"
        "После поступления оплаты мы создадим бота и пришлём письмо со ссылкой на панель управления.",
    )


def rejected(name: str, reason: str) -> tuple[str, str]:
    return (
        "Заявка Nox Bot отклонена",
        f"Здравствуйте, {name}!\n\n"
        f"К сожалению, мы не можем подключить бота по вашей заявке.\n\nПричина: {reason}\n\n"
        "Если есть вопросы, ответьте на это письмо или позвоните нам.",
    )


def bot_ready(name: str, admin_url: str, setup_url: str | None) -> tuple[str, str]:
    setup = (
        "Создайте учётную запись владельца по одноразовой ссылке (не пересылайте её никому):\n"
        f"{setup_url}\n\n"
        if setup_url
        else ""
    )
    return (
        "Ваш бот Nox Bot готов",
        f"Здравствуйте, {name}!\n\n"
        f"Ваш бот готов. Панель управления: {admin_url}\n\n"
        f"{setup}"
        f"Статус и ссылки также доступны в личном кабинете: {_portal()}",
    )


def suspended(name: str) -> tuple[str, str]:
    return (
        "Бот Nox Bot приостановлен",
        f"Здравствуйте, {name}!\n\nРабота вашего бота приостановлена. Данные сохранены. "
        "Чтобы возобновить работу, свяжитесь с нами.",
    )


def resumed(name: str, admin_url: str) -> tuple[str, str]:
    return (
        "Бот Nox Bot снова работает",
        f"Здравствуйте, {name}!\n\nРабота вашего бота возобновлена. Панель управления: {admin_url}",
    )


def _date(value) -> str:
    from zoneinfo import ZoneInfo

    return value.astimezone(ZoneInfo(get_settings().tz)).strftime("%d.%m.%Y")


def _n(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def renewal_invoice(name: str, number: str, tariff_name: str, amount: str, period_start,
                    grace_days: int, comment: str = "") -> tuple[str, str]:
    return (
        f"Счёт {number}: продление Nox Bot",
        f"Здравствуйте, {name}!\n\n"
        f"Выставлен счёт {number} за следующий месяц обслуживания Nox Bot (тариф «{tariff_name}») на {amount}. "
        f"Новый месяц начинается {_date(period_start)}, тогда же начисляется месячный пакет коинов.\n\n"
        f"{_comment(comment)}"
        f"Счёт для печати (PDF) — в личном кабинете: {_portal()}/cabinet\n\n"
        f"Если оплата не поступит в течение {grace_days} дней после начала нового месяца, работа бота будет "
        "приостановлена (данные сохранятся).",
    )


def topup_invoice(name: str, number: str, amount: str, coins: int, comment: str = "") -> tuple[str, str]:
    return (
        f"Счёт {number}: пакет коинов Nox Bot",
        f"Здравствуйте, {name}!\n\n"
        f"Выставлен счёт {number} на пакет {_n(coins)} коинов: {amount}.\n"
        "Коины начислятся сразу после поступления оплаты и не сгорают в конце месяца.\n\n"
        f"{_comment(comment)}"
        f"Счёт для печати (PDF): {_portal()}/cabinet",
    )


def upgrade_invoice(name: str, number: str, tariff_name: str, amount: str, comment: str = "") -> tuple[str, str]:
    return (
        f"Счёт {number}: тариф «{tariff_name}» Nox Bot",
        f"Здравствуйте, {name}!\n\n"
        f"Выставлен счёт {number} на переход с тестового тарифа на «{tariff_name}»: {amount} за месяц.\n"
        "После поступления оплаты бот продолжит работу на новом тарифе, настройки сохранятся.\n\n"
        f"{_comment(comment)}"
        f"Счёт для печати (PDF) — в личном кабинете: {_portal()}/cabinet",
    )


def staff_invoice_issued(company: str, number: str, what: str, amount: str) -> tuple[str, str]:
    return (
        f"Выставлен счёт {number}: {company}",
        f"{what}: счёт {number} на {amount}.\n\nПосле поступления оплаты отметьте её в портале: {_portal()}/staff",
    )


def coins_added(name: str, coins: int) -> tuple[str, str]:
    return (
        "Коины Nox Bot начислены",
        f"Здравствуйте, {name}!\n\nОплата получена, на баланс начислено {_n(coins)} коинов.\n\n"
        f"Баланс и расход: {_portal()}/cabinet",
    )


def low_balance(name: str, left: int, percent: int) -> tuple[str, str]:
    return (
        f"Nox Bot: осталось {percent}% коинов",
        f"Здравствуйте, {name}!\n\nНа балансе осталось {_n(left)} коинов (около {percent}% пакета). Когда коины "
        "закончатся, бот перестанет отвечать сам и будет передавать диалоги оператору.\n\n"
        f"Пополнить баланс: {_portal()}/cabinet",
    )


def suspended_unpaid(name: str) -> tuple[str, str]:
    return (
        "Бот Nox Bot приостановлен: нет оплаты",
        f"Здравствуйте, {name}!\n\nСчёт за продление не оплачен, поэтому работа бота приостановлена. Данные "
        f"сохранены. После оплаты бот снова заработает. Счёт — в личном кабинете: {_portal()}/cabinet",
    )


def staff_suspended_unpaid(company: str, number: str) -> tuple[str, str]:
    return (
        f"Приостановлен за неоплату: {company}",
        f"Счёт {number} не оплачен после отсрочки: аккаунт приостановлен, ключ шлюза отключён.\n\n{_portal()}/staff",
    )


def local_account(name: str, email: str) -> tuple[str, str]:
    return (
        "Личный кабинет Nox Bot",
        f"Здравствуйте, {name}!\n\nДля вашего бота Nox Bot создан личный кабинет: в нём видно баланс коинов, "
        "расход и счета, там же можно пополнить баланс.\n\n"
        f"Чтобы войти, задайте пароль: {_portal()}/reset (e-mail: {email})",
    )
