from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings


logger = logging.getLogger(__name__)


class OTPDispatchError(Exception):
    pass


async def send_otp_code(*, channel: str, destination: str, otp_code: str, purpose: str) -> None:
    channel_value = channel.strip().lower()
    if channel_value == "email":
        await _send_email_otp(destination=destination, otp_code=otp_code, purpose=purpose)
        return

    if channel_value == "mobile":
        raise OTPDispatchError("Mobile OTP dispatch is not configured yet.")

    raise OTPDispatchError(f"Unsupported OTP channel: {channel}")


async def _send_email_otp(*, destination: str, otp_code: str, purpose: str) -> None:
    settings = get_settings()

    host = (settings.smtp_host or "").strip()
    if not host:
        raise OTPDispatchError("SMTP is not configured. Set SMTP_HOST to enable email OTP delivery.")

    from_email = (settings.smtp_from_email or settings.smtp_username or "").strip()
    if not from_email:
        raise OTPDispatchError("SMTP sender is not configured. Set SMTP_FROM_EMAIL or SMTP_USERNAME.")

    subject = f"{settings.smtp_subject_prefix} OTP code"
    body = _build_otp_email_text(
        otp_code=otp_code,
        purpose=purpose,
        expiration_minutes=settings.otp_exp_minutes,
        app_name=settings.smtp_app_name,
    )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = _format_from_header(from_email=from_email, from_name=settings.smtp_from_name)
    message["To"] = destination
    message.set_content(body)

    await asyncio.to_thread(_smtp_send_message, message)
    logger.info("OTP email sent destination=%s purpose=%s", _mask_email(destination), purpose)


def _smtp_send_message(message: EmailMessage) -> None:
    settings = get_settings()

    smtp_cls = smtplib.SMTP_SSL if settings.smtp_use_ssl else smtplib.SMTP
    try:
        with smtp_cls(
            host=settings.smtp_host,
            port=settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
        ) as client:
            if not settings.smtp_use_ssl and settings.smtp_starttls:
                client.starttls()

            username = (settings.smtp_username or "").strip()
            password = settings.smtp_password or ""
            if settings.smtp_require_auth:
                if not username or not password:
                    raise OTPDispatchError(
                        "SMTP authentication is enabled but SMTP_USERNAME/SMTP_PASSWORD is missing."
                    )
                client.login(username, password)
            elif username and password:
                client.login(username, password)

            client.send_message(message)
    except OTPDispatchError:
        raise
    except (smtplib.SMTPException, OSError) as exc:
        raise OTPDispatchError(f"SMTP delivery failed: {exc}") from exc


def _build_otp_email_text(*, otp_code: str, purpose: str, expiration_minutes: int, app_name: str) -> str:
    safe_purpose = (purpose or "verification").strip().lower()
    return (
        f"Your {app_name} OTP code for {safe_purpose} is: {otp_code}\n\n"
        f"This code expires in {expiration_minutes} minutes.\n"
        "If you did not request this code, you can ignore this email."
    )


def _format_from_header(*, from_email: str, from_name: str | None) -> str:
    clean_name = (from_name or "").strip()
    if clean_name:
        return f"{clean_name} <{from_email}>"
    return from_email


def _mask_email(email: str) -> str:
    value = email.strip()
    if "@" not in value:
        return "***"
    local, domain = value.split("@", 1)
    if len(local) <= 2:
        masked_local = "*" * len(local)
    else:
        masked_local = local[:1] + "*" * (len(local) - 2) + local[-1:]
    return f"{masked_local}@{domain}"
