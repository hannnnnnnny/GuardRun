from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.types.beta import BetaMessage

from guardrun.errors import ProviderError
from guardrun.providers.anthropic import (
    DEFAULT_MODEL,
    FALLBACK_BETA,
    AnthropicProvider,
    to_model_response,
)
from guardrun.providers.base import ModelRequest

MESSAGE = BetaMessage.model_validate(
    {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": DEFAULT_MODEL,
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig-abc"},
            {"type": "text", "text": "Reading the file."},
            {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"path": "a"}},
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_input_tokens": 5,
            "cache_creation_input_tokens": 0,
        },
    }
)

TOOL = {"name": "read_file", "description": "d", "input_schema": {"type": "object"}}
REQUEST = ModelRequest(system="sys", messages=[{"role": "user", "content": "hi"}], tools=[TOOL])


class FakeStream:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome

    def __enter__(self) -> FakeStream:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get_final_message(self) -> Any:
        return self.outcome


class FakeClient:
    def __init__(self, outcome: Any) -> None:
        self.calls: list[dict[str, Any]] = []

        def stream(**params: Any) -> FakeStream:
            self.calls.append(params)
            return FakeStream(outcome)

        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=stream))


def test_conversion_keeps_thinking_blocks_verbatim() -> None:
    response = to_model_response(MESSAGE)
    assert response.content[0] == {"type": "thinking", "thinking": "", "signature": "sig-abc"}
    assert response.text == "Reading the file."
    assert response.tool_calls[0].input == {"path": "a"}
    assert response.usage.cache_read_input_tokens == 5


def test_request_parameters() -> None:
    client = FakeClient(MESSAGE)
    AnthropicProvider(client=client, effort="xhigh").complete(REQUEST)
    params = client.calls[0]
    assert params["model"] == "claude-opus-5-5"
    assert params["thinking"] == {"type": "adaptive"}
    assert params["output_config"] == {"effort": "xhigh"}
    assert params["cache_control"] == {"type": "ephemeral"}
    assert params["betas"] == [FALLBACK_BETA]
    assert params["fallbacks"] == "default"
    assert params["tools"][0]["eager_input_streaming"] is True
    assert "eager_input_streaming" not in TOOL


def test_fallbacks_can_be_disabled_and_tools_omitted() -> None:
    client = FakeClient(MESSAGE)
    AnthropicProvider(client=client, fallbacks=False).complete(
        ModelRequest(system="s", messages=[])
    )
    assert "betas" not in client.calls[0]
    assert "tools" not in client.calls[0]


def test_invalid_effort_rejected() -> None:
    with pytest.raises(ValueError):
        AnthropicProvider(client=FakeClient(MESSAGE), effort="extreme")


def _status_error(cls: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request)
    return cls("boom", response=response, body=None)


@pytest.mark.parametrize(
    ("error", "message", "transient"),
    [
        (_status_error(anthropic.AuthenticationError, 401), "authentication failed", False),
        (_status_error(anthropic.RateLimitError, 429), "rate limited", True),
        (_status_error(anthropic.BadRequestError, 400), "rejected the request", False),
        (_status_error(anthropic.InternalServerError, 500), "API error 500", True),
        (_status_error(anthropic.APIStatusError, 529), "API error 529", True),
        (_status_error(anthropic.APIStatusError, 404), "API error 404", False),
    ],
)
def test_api_errors_become_provider_errors(error: Exception, message: str, transient: bool) -> None:
    with pytest.raises(ProviderError, match=message) as caught:
        AnthropicProvider(client=FakeClient(error)).complete(REQUEST)
    assert caught.value.transient is transient


def test_connection_errors_become_provider_errors() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com")
    error = anthropic.APIConnectionError(request=request)
    with pytest.raises(ProviderError, match="could not reach") as caught:
        AnthropicProvider(client=FakeClient(error)).complete(REQUEST)
    assert caught.value.transient
