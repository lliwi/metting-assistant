import smtplib
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage
from typing import Protocol

from app.core.config import get_settings
from app.notifications.templates import render


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    body: str


class EmailOutbox(Protocol):
    def send(self, email: Email) -> None: ...


class CeleryEmailOutbox:
    """Encola el envío: la API no espera al servidor SMTP y el worker reintenta si falla."""

    def send(self, email: Email) -> None:
        from app.worker.tasks import send_email

        send_email.delay(email.to, email.subject, email.body)


def get_email_outbox() -> EmailOutbox:
    return CeleryEmailOutbox()


def build_email(template: str, *, to: str, locale: str, **context: str) -> Email:
    subject, body = render(template, locale, **context)
    return Email(to=to, subject=subject, body=body)


def deliver_via_smtp(to: str, subject: str, body: str) -> None:
    settings = get_settings()
    message = MimeMessage()
    message["From"] = settings.email_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        smtp.send_message(message)
