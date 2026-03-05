import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx


logger = logging.getLogger(__name__)


class GeminiClient:
    """Small adapter for Gemini REST API with safe fallback mode."""

    def __init__(
        self,
        api_key: str | None,
        model: str = "gemini-2.5-flash",
        timeout_seconds: int = 5,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def generate_reply(
        self,
        *,
        system_prompt: str,
        user_message: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        if not self.api_key:
            return self._fallback_reply(system_prompt=system_prompt, user_message=user_message)

        payload = self._build_payload(
            system_prompt=system_prompt,
            user_message=user_message,
            history=history or [],
        )
        try:
            return await self._call_api(payload)
        except Exception as exc:
            logger.warning("Gemini request failed, using fallback response: %s", exc)
            return self._fallback_reply(system_prompt=system_prompt, user_message=user_message)

    async def stream_reply(
        self,
        *,
        system_prompt: str,
        user_message: str,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        if not self.api_key:
            yield self._fallback_reply(system_prompt=system_prompt, user_message=user_message)
            return

        payload = self._build_payload(
            system_prompt=system_prompt,
            user_message=user_message,
            history=history or [],
        )
        try:
            emitted = False
            async for chunk in self._stream_api(payload):
                emitted = True
                yield chunk
            if not emitted:
                text = await self._call_api(payload)
                if text:
                    yield text
        except Exception as exc:
            logger.warning("Gemini stream request failed, using fallback response: %s", exc)
            yield self._fallback_reply(system_prompt=system_prompt, user_message=user_message)

    def _build_payload(
        self,
        *,
        system_prompt: str,
        user_message: str,
        history: list[dict[str, str]],
    ) -> dict[str, Any]:
        history_text = "\n".join(
            f"{turn.get('role', 'user')}: {turn.get('content', '')}" for turn in history[-10:]
        )
        prompt = (
            f"System instructions:\n{system_prompt}\n\n"
            f"Recent conversation:\n{history_text or 'No history.'}\n\n"
            f"User message:\n{user_message}\n\n"
            "Respond naturally and keep it concise."
        )
        return {
            "contents": [
                {
                    "parts": [{"text": prompt}],
                }
            ]
        }

    async def _call_api(self, payload: dict[str, Any]) -> str:
        timeout = httpx.Timeout(timeout=self.timeout_seconds, connect=self.timeout_seconds)
        models_to_try = [self.model, "gemini-2.5-flash", "gemini-2.0-flash"]
        tried: set[str] = set()
        last_error: Exception | None = None

        async with httpx.AsyncClient(timeout=timeout) as client:
            for model in models_to_try:
                if model in tried:
                    continue
                tried.add(model)
                url = (
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model}:generateContent?key={self.api_key}"
                )
                try:
                    response = await client.post(
                        url,
                        content=json.dumps(payload),
                        headers={"Content-Type": "application/json"},
                    )
                    response.raise_for_status()
                    data = response.json()
                    text = self._extract_text(data)
                    if text:
                        return text
                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    if exc.response.status_code in (400, 404):
                        continue
                    raise

        if last_error:
            raise last_error
        return self._fallback_reply(
            system_prompt="",
            user_message=payload["contents"][0]["parts"][0]["text"],
        )

    async def _stream_api(self, payload: dict[str, Any]) -> AsyncIterator[str]:
        timeout = httpx.Timeout(timeout=self.timeout_seconds * 4, connect=self.timeout_seconds)
        models_to_try = [self.model, "gemini-2.5-flash", "gemini-2.0-flash"]
        tried: set[str] = set()
        last_error: Exception | None = None

        async with httpx.AsyncClient(timeout=timeout) as client:
            for model in models_to_try:
                if model in tried:
                    continue
                tried.add(model)
                url = (
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model}:streamGenerateContent?alt=sse&key={self.api_key}"
                )

                try:
                    async with client.stream(
                        "POST",
                        url,
                        content=json.dumps(payload),
                        headers={"Content-Type": "application/json"},
                    ) as response:
                        response.raise_for_status()
                        emitted = False
                        aggregated_text = ""

                        async for line in response.aiter_lines():
                            if not line:
                                continue
                            if not line.startswith("data:"):
                                continue

                            data = line[len("data:") :].strip()
                            if not data or data == "[DONE]":
                                continue

                            try:
                                event = json.loads(data)
                            except json.JSONDecodeError:
                                continue

                            text = self._extract_text(event)
                            if not text:
                                continue

                            delta = self._extract_stream_delta(aggregated_text, text)
                            if not delta:
                                continue

                            emitted = True
                            aggregated_text += delta
                            yield delta

                        if emitted:
                            return
                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    if exc.response.status_code in (400, 404):
                        continue
                    raise

        if last_error:
            raise last_error

    def _extract_stream_delta(self, previous: str, current: str) -> str:
        if not previous:
            return current
        if current.startswith(previous):
            return current[len(previous) :]
        return current

    def _extract_text(self, data: dict[str, Any]) -> str | None:
        candidates = data.get("candidates", [])
        if not candidates:
            return None

        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        if not parts:
            return None

        text = parts[0].get("text")
        if not isinstance(text, str):
            return None

        return text.strip() or None

    def _fallback_reply(self, *, system_prompt: str, user_message: str) -> str:
        return f"Assistant mode: I can help with '{user_message.strip()}'. What should we do first?"
