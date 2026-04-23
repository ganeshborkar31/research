from __future__ import annotations

import base64
import hashlib
import hmac
from xml.sax.saxutils import escape

import httpx

from app.core.config import get_settings


class TwilioConfigurationError(RuntimeError):
    """Raised when Twilio credentials are missing or invalid."""


class TwilioRequestError(RuntimeError):
    """Raised when Twilio REST API calls fail."""


class TwilioService:
    api_base = "https://api.twilio.com/2010-04-01"

    def __init__(self) -> None:
        self._settings = get_settings()

    def is_configured(self) -> bool:
        return bool(
            self._settings.twilio_account_sid
            and self._settings.twilio_auth_token
            and self._settings.twilio_phone_number
        )

    def validate_webhook_signature(
        self,
        *,
        signature: str | None,
        url: str,
        form_data: dict[str, str],
    ) -> bool:
        """Validate X-Twilio-Signature for webhook authenticity.

        Twilio signature spec:
        signature = Base64(HMAC-SHA1(AuthToken, url + concat(sorted(params))))
        """
        auth_token = (self._settings.twilio_auth_token or "").strip()
        if not auth_token:
            return False
        if not signature:
            return False

        payload = url + "".join(f"{k}{v}" for k, v in sorted(form_data.items()))
        digest = hmac.new(auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
        expected = base64.b64encode(digest).decode("utf-8")
        return hmac.compare_digest(expected, signature.strip())

    async def send_sms(
        self,
        *,
        to_number: str,
        body: str,
        from_number: str | None = None,
        status_callback_url: str | None = None,
    ) -> dict:
        settings = self._settings
        sid = (settings.twilio_account_sid or "").strip()
        token = (settings.twilio_auth_token or "").strip()
        sender = (from_number or settings.twilio_phone_number or "").strip()

        if not sid or not token or not sender:
            raise TwilioConfigurationError(
                "Twilio is not configured. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_PHONE_NUMBER."
            )

        payload = {
            "To": to_number.strip(),
            "From": sender,
            "Body": body.strip(),
        }
        if status_callback_url:
            payload["StatusCallback"] = status_callback_url.strip()

        if not payload["To"] or not payload["Body"]:
            raise ValueError("Both 'to_number' and 'body' are required.")

        url = f"{self.api_base}/Accounts/{sid}/Messages.json"
        response = await self._post(url=url, data=payload, auth=(sid, token))
        return {
            "sid": response.get("sid"),
            "status": response.get("status"),
            "to": response.get("to"),
            "from": response.get("from"),
            "direction": response.get("direction"),
        }

    async def create_call(
        self,
        *,
        to_number: str,
        twiml_url: str,
        from_number: str | None = None,
        status_callback_url: str | None = None,
    ) -> dict:
        settings = self._settings
        sid = (settings.twilio_account_sid or "").strip()
        token = (settings.twilio_auth_token or "").strip()
        sender = (from_number or settings.twilio_phone_number or "").strip()

        if not sid or not token or not sender:
            raise TwilioConfigurationError(
                "Twilio is not configured. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_PHONE_NUMBER."
            )

        payload = {
            "To": to_number.strip(),
            "From": sender,
            "Url": twiml_url.strip(),
            "Method": "POST",
        }
        if status_callback_url:
            payload["StatusCallback"] = status_callback_url.strip()

        if not payload["To"] or not payload["Url"]:
            raise ValueError("Both 'to_number' and 'twiml_url' are required.")

        url = f"{self.api_base}/Accounts/{sid}/Calls.json"
        response = await self._post(url=url, data=payload, auth=(sid, token))
        return {
            "sid": response.get("sid"),
            "status": response.get("status"),
            "to": response.get("to"),
            "from": response.get("from"),
            "direction": response.get("direction"),
        }

    async def _post(self, *, url: str, data: dict, auth: tuple[str, str]) -> dict:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(url, data=data, auth=auth)
        except Exception as exc:
            raise TwilioRequestError(f"Twilio request failed: {exc}") from exc

        parsed: dict
        try:
            parsed = response.json()
        except Exception:
            parsed = {}

        if response.status_code >= 400:
            message = parsed.get("message") if isinstance(parsed, dict) else None
            detail = message or response.text
            raise TwilioRequestError(f"Twilio API error ({response.status_code}): {detail}")

        if not isinstance(parsed, dict):
            raise TwilioRequestError("Twilio API response was not valid JSON.")
        return parsed

    def voice_gather_twiml(self, *, say_text: str, action_url: str) -> str:
        safe_text = escape(say_text.strip() or "Please say that again.")
        safe_action = escape(action_url.strip())
        return (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            "<Response>"
            f"<Gather input=\"speech dtmf\" speechTimeout=\"auto\" timeout=\"5\" method=\"POST\" action=\"{safe_action}\">"
            f"<Say>{safe_text}</Say>"
            "</Gather>"
            "<Say>I did not hear anything. Goodbye.</Say>"
            "<Hangup/>"
            "</Response>"
        )

    def sms_twiml(self, *, message: str) -> str:
        safe_message = escape(message.strip() or "Thanks, we received your message.")
        return (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            "<Response>"
            f"<Message>{safe_message}</Message>"
            "</Response>"
        )


_twilio_service: TwilioService | None = None


def get_twilio_service() -> TwilioService:
    global _twilio_service
    if _twilio_service is None:
        _twilio_service = TwilioService()
    return _twilio_service
