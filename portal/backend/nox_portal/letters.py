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


def staff_tariff_chosen(company: str, number: str, tariff_name: str, amount: str) -> tuple[str, str]:
    return (
        f"Выставлен счёт {number}: {company}",
        f"Клиент выбрал тариф «{tariff_name}», счёт {number} на {amount}.\n\n"
        f"После поступления оплаты отметьте её в портале: {_portal()}/staff",
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


def invoice(name: str, number: str, tariff_name: str, amount: str, due_days: int) -> tuple[str, str]:
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
                    grace_days: int) -> tuple[str, str]:
    return (
        f"Счёт {number}: продление Nox Bot",
        f"Здравствуйте, {name}!\n\n"
        f"Выставлен счёт {number} за следующий месяц обслуживания Nox Bot (тариф «{tariff_name}») на {amount}. "
        f"Новый месяц начинается {_date(period_start)}, тогда же начисляется месячный пакет коинов.\n\n"
        f"Счёт для печати (PDF) — в личном кабинете: {_portal()}/cabinet\n\n"
        f"Если оплата не поступит в течение {grace_days} дней после начала нового месяца, работа бота будет "
        "приостановлена (данные сохранятся).",
    )


def topup_invoice(name: str, number: str, amount: str, coins: int) -> tuple[str, str]:
    return (
        f"Счёт {number}: пакет коинов Nox Bot",
        f"Здравствуйте, {name}!\n\n"
        f"Выставлен счёт {number} на пакет {_n(coins)} коинов: {amount}.\n"
        "Коины начислятся сразу после поступления оплаты и не сгорают в конце месяца.\n\n"
        f"Счёт для печати (PDF): {_portal()}/cabinet",
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
