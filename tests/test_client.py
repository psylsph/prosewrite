import json
from unittest.mock import patch

import httpx
import pytest

from prosewrite.client import LLMClient
from prosewrite.config import StageSettings
from prosewrite.exceptions import LLMError, LLMTimeoutError


def _make_settings(**overrides) -> StageSettings:
    base = dict(
        api_base_url="https://api.example.com/v1",
        api_key_env="TEST_API_KEY",
        model="test-model",
        temperature=0.7,
        max_tokens=1024,
        timeout_s=30,
    )
    base.update(overrides)
    return StageSettings(**base)


def _ok_response(content: str = "Hello from LLM") -> dict:
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content}}
        ]
    }


class MockTransport(httpx.BaseTransport):
    def __init__(self, responses: list[tuple[int, dict]]):
        self._responses = iter(responses)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        status, body = next(self._responses)
        return httpx.Response(status, json=body)


class TestLLMClientSuccess:
    def test_returns_assistant_content(self):
        settings = _make_settings()
        transport = MockTransport([(200, _ok_response("Test response"))])
        with patch("httpx.Client", return_value=httpx.Client(transport=transport)):
            client = LLMClient(settings)
            with patch.dict("os.environ", {"TEST_API_KEY": "fake-key"}):
                result = client.complete("system prompt", [{"role": "user", "content": "hello"}])
        assert result == "Test response"

    def test_sends_correct_payload(self):
        settings = _make_settings()
        captured: list[dict] = []

        class CapturingTransport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                captured.append(json.loads(request.content))
                return httpx.Response(200, json=_ok_response())

        transport = CapturingTransport()
        with patch("httpx.Client", return_value=httpx.Client(transport=transport)):
            client = LLMClient(settings)
            with patch.dict("os.environ", {"TEST_API_KEY": "fake-key"}):
                client.complete("my system", [{"role": "user", "content": "my message"}])

        assert len(captured) == 1
        payload = captured[0]
        assert payload["model"] == "test-model"
        assert payload["temperature"] == 0.7
        assert payload["messages"][0] == {"role": "system", "content": "my system"}
        assert payload["messages"][1] == {"role": "user", "content": "my message"}


class TestLLMClientErrors:
    def test_non_200_raises_llm_error(self):
        settings = _make_settings()
        transport = MockTransport([(429, {"error": "rate limited"})])
        with patch("httpx.Client", return_value=httpx.Client(transport=transport)):
            client = LLMClient(settings)
            with patch.dict("os.environ", {"TEST_API_KEY": "fake-key"}):
                with pytest.raises(LLMError) as exc_info:
                    client.complete("sys", [{"role": "user", "content": "hi"}])
        assert exc_info.value.status_code == 429

    def test_malformed_response_raises_llm_error(self):
        settings = _make_settings()
        transport = MockTransport([(200, {"unexpected": "shape"})])
        with patch("httpx.Client", return_value=httpx.Client(transport=transport)):
            client = LLMClient(settings)
            with patch.dict("os.environ", {"TEST_API_KEY": "fake-key"}):
                with pytest.raises(LLMError, match="Unexpected response"):
                    client.complete("sys", [{"role": "user", "content": "hi"}])

    def test_timeout_raises_llm_timeout_error(self):
        settings = _make_settings()

        class TimeoutTransport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                raise httpx.ReadTimeout("timed out", request=request)

        with patch("httpx.Client", return_value=httpx.Client(transport=TimeoutTransport())):
            client = LLMClient(settings)
            with patch.dict("os.environ", {"TEST_API_KEY": "fake-key"}):
                with pytest.raises(LLMTimeoutError):
                    client.complete("sys", [{"role": "user", "content": "hi"}])

    def test_context_manager(self):
        settings = _make_settings()
        transport = MockTransport([(200, _ok_response())])
        with patch("httpx.Client", return_value=httpx.Client(transport=transport)):
            with patch.dict("os.environ", {"TEST_API_KEY": "fake-key"}):
                with LLMClient(settings) as client:
                    result = client.complete("sys", [{"role": "user", "content": "hi"}])
        assert result == "Hello from LLM"
