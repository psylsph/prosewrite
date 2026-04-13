from __future__ import annotations

import httpx

from .config import StageSettings
from .exceptions import LLMError, LLMTimeoutError

CHAT_COMPLETIONS_PATH = "/chat/completions"


class LLMClient:
    """Thin wrapper around any OpenAI-compatible chat completions endpoint."""

    def __init__(self, settings: StageSettings):
        self._settings = settings
        base = settings.api_base_url.rstrip("/")
        self._url = base + CHAT_COMPLETIONS_PATH
        self._client = httpx.Client(timeout=settings.timeout_s)

    def complete(self, system: str, messages: list[dict]) -> str:
        """Send a chat completion request and return the assistant's reply text."""
        payload = {
            "model": self._settings.model,
            "temperature": self._settings.temperature,
            "max_tokens": self._settings.max_tokens,
            "messages": [{"role": "system", "content": system}, *messages],
        }
        headers = {
            "Authorization": f"Bearer {self._settings.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = self._client.post(self._url, json=payload, headers=headers)
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(
                f"Request to {self._url} timed out after {self._settings.timeout_s}s"
            ) from e
        except httpx.RequestError as e:
            raise LLMError(f"Network error calling {self._url}: {e}") from e

        if response.status_code != 200:
            raise LLMError(
                f"LLM API returned {response.status_code}: {response.text[:400]}",
                status_code=response.status_code,
            )

        try:
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError(f"Unexpected response structure from LLM API: {e}\n{response.text[:400]}") from e

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "LLMClient":
        return self

    def __exit__(self, *_) -> None:
        self.close()
