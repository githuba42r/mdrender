# server/app/mail.py
"""Outbound email over SMTP. An empty SMTP_HOST means email is not set up."""
import logging
import ssl

import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)


def configured(config) -> bool:
    return bool(getattr(config, "SMTP_HOST", ""))


def send(config, to: str, subject: str, body: str) -> bool:
    """Send one plain-text email. Returns False when SMTP is not configured."""
    if not configured(config):
        return False
    msg = EmailMessage()
    msg["From"] = config.SMTP_FROM or config.SMTP_USER or "no-reply@localhost"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    host = config.SMTP_HOST
    port = int(config.SMTP_PORT or 587)
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30, context=ctx) as smtp:
            if config.SMTP_USER:
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)
        return True
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        try:
            smtp.starttls(context=ctx)
        except smtplib.SMTPNotSupportedError:
            pass  # plain relay
        if config.SMTP_USER:
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.send_message(msg)
    return True
