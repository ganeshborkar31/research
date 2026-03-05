from __future__ import annotations

from email.message import EmailMessage
from types import SimpleNamespace

import pytest

from app.infra.notifications.otp_dispatcher import OTPDispatchError, send_otp_code


class _DummySMTP:
    last_instance: _DummySMTP | None = None

    def __init__(self, host: str, port: int, timeout: int):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.starttls_called = False
        self.login_called_with: tuple[str, str] | None = None
        self.sent_message: EmailMessage | None = None
        _DummySMTP.last_instance = self

    def __enter__(self) -> _DummySMTP:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def starttls(self) -> None:
        self.starttls_called = True

    def login(self, username: str, password: str) -> None:
        self.login_called_with = (username, password)

    def send_message(self, message: EmailMessage) -> None:
        self.sent_message = message


def _settings(**overrides):
    base = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_timeout_seconds": 15,
        "smtp_use_ssl": False,
        "smtp_starttls": True,
        "smtp_username": "mailer@example.com",
        "smtp_password": "secret",
        "smtp_require_auth": True,
        "smtp_from_email": "no-reply@example.com",
        "smtp_from_name": "Knowledge AI",
        "smtp_subject_prefix": "Knowledge AI",
        "smtp_app_name": "Knowledge AI",
        "otp_exp_minutes": 10,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_send_email_otp_via_smtp(monkeypatch):
    import app.infra.notifications.otp_dispatcher as dispatcher

    monkeypatch.setattr(dispatcher, "get_settings", lambda: _settings())
    monkeypatch.setattr(dispatcher.smtplib, "SMTP", _DummySMTP)

    await send_otp_code(
        channel="email",
        destination="user@example.com",
        otp_code="123456",
        purpose="signup",
    )

    smtp = _DummySMTP.last_instance
    assert smtp is not None
    assert smtp.host == "smtp.example.com"
    assert smtp.port == 587
    assert smtp.starttls_called is True
    assert smtp.login_called_with == ("mailer@example.com", "secret")
    assert smtp.sent_message is not None
    assert smtp.sent_message["To"] == "user@example.com"
    assert "123456" in smtp.sent_message.get_content()


@pytest.mark.asyncio
async def test_send_email_otp_requires_smtp_host(monkeypatch):
    import app.infra.notifications.otp_dispatcher as dispatcher

    monkeypatch.setattr(dispatcher, "get_settings", lambda: _settings(smtp_host=None))

    with pytest.raises(OTPDispatchError, match="SMTP_HOST"):
        await send_otp_code(
            channel="email",
            destination="user@example.com",
            otp_code="123456",
            purpose="signup",
        )


@pytest.mark.asyncio
async def test_send_mobile_otp_not_configured():
    with pytest.raises(OTPDispatchError, match="Mobile OTP dispatch"):
        await send_otp_code(
            channel="mobile",
            destination="+911234567890",
            otp_code="654321",
            purpose="login",
        )
