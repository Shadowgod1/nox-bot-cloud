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


def staff_new_application(company: str, contact: str, email: str, phone: str, slug: str) -> tuple[str, str]:
    domain = get_settings().instance_domain
    return (
        f"Новая заявка Nox Bot: {company}",
        "Подтверждена новая заявка на бота.\n\n"
        f"Компания: {company}\nКонтакт: {contact}\nE-mail: {email}\nТелефон: {phone}\n"
        f"Адрес: {slug}.{domain}\n\n"
        f"Рассмотреть: {_portal()}/staff",
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
