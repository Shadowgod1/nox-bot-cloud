"""Outgoing e-mail: SMTP (STARTTLS / SSL), a dev mode that prints to the log, and an in-memory outbox
for tests. Every letter is plain text plus a simple HTML part, in Russian, signed "Nox service"."""

import asyncio
import html
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from nox_portal.config import get_settings, whatsapp_url

log = logging.getLogger("nox_portal.mail")


def signature() -> str:
    """Added to every letter; the support phone and WhatsApp only when NOX_SUPPORT_PHONE / NOX_SUPPORT_WHATSAPP are set."""
    lines = ["С уважением,", "Nox service"]
    phone = get_settings().support_phone.strip()
    if phone:
        lines.append(f"Телефон: {phone}")
    if whatsapp_url():
        lines.append(f"WhatsApp: {whatsapp_url()}")
    lines.append("https://noxsw.kz")
    return "\n".join(lines)


@dataclass
class Mail:
    to: str
    subject: str
    text: str


# memory mode (tests): letters land here
OUTBOX: list[Mail] = []


class MailError(Exception):
    pass


def _html(text: str) -> str:
    paragraphs = "".join(
        f"<p style=\"margin:0 0 12px\">{html.escape(p).replace(chr(10), '<br>')}</p>"
        for p in text.split("\n\n")
    )
    return (
        "<!doctype html><html lang=\"ru\"><body style=\"margin:0;background:#e3e6ea;padding:24px;"
        "font-family:'Nunito Sans',Arial,sans-serif;color:#2f353b;font-size:15px;line-height:1.5\">"
        "<div style=\"max-width:560px;margin:0 auto;background:#f4f5f7;border:1px solid #d9dde2;"
        "border-radius:6px;padding:24px\">"
        "<div style=\"font-weight:700;font-size:18px;color:#3f6ad8;margin-bottom:16px\">Nox Bot</div>"
        f"{paragraphs}</div></body></html>"
    )


def _build(mail: Mail) -> EmailMessage:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = formataddr(("Nox service", s.smtp_from)) if "<" not in s.smtp_from else s.smtp_from
    msg["To"] = mail.to
    msg["Subject"] = mail.subject
    if s.smtp_reply_to.strip():
        msg["Reply-To"] = s.smtp_reply_to.strip()
    msg["Message-ID"] = make_msgid(domain=(s.smtp_from.rsplit("@", 1)[-1].strip(">") or "noxsw.kz"))
    body = f"{mail.text}\n\n{signature()}\n"
    msg.set_content(body)
    msg.add_alternative(_html(body), subtype="html")
    return msg


def _send_smtp(mail: Mail) -> None:
    s = get_settings()
    if not s.smtp_host or not s.smtp_from:
        raise MailError("SMTP is not configured (NOX_SMTP_HOST / NOX_SMTP_FROM)")
    msg = _build(mail)
    ctx = ssl.create_default_context()
    try:
        if s.smtp_security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=20, context=ctx)
        else:
            server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20)
        with server:
            if s.smtp_security == "starttls":
                server.starttls(context=ctx)
            if s.smtp_user:
                server.login(s.smtp_user, s.smtp_password)
            server.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        # never include credentials; the exception text from smtplib does not contain them
        raise MailError(f"{type(exc).__name__}: {exc}") from exc


async def send(to: str, subject: str, text: str) -> None:
    mail = Mail(to=to, subject=subject, text=text)
    mode = get_settings().email_mode
    if mode == "memory":
        OUTBOX.append(mail)
        return
    if mode == "log":
        # Dev only: letters (including codes and links) are printed. Never use in production.
        log.warning("DEV MAIL to=%s subject=%s\n%s", to, subject, text)
        return
    await asyncio.to_thread(_send_smtp, mail)


async def send_quiet(to: str, subject: str, text: str) -> bool:
    """Best effort (notifications): log the failure, never raise."""
    try:
        await send(to, subject, text)
        return True
    except MailError as exc:
        log.error("mail to %s failed: %s", to, exc)
        return False


def staff_recipients() -> list[str]:
    return [a.strip() for a in get_settings().staff_notify.split(",") if a.strip()]
