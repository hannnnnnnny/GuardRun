"""A deterministic provider that replays scripted turns (for tests and demos)."""

from __future__ import annotations

import itertools
from collections.abc import Iterable
from typing import Any

from guardrun.errors import ProviderError
from guardrun.messages import JSON, ModelResponse
from guardrun.providers.base import ModelRequest
from guardrun.usage import Usage

_ids = itertools.count(1)


def text_turn(text: str, usage: Usage | None = None) -> ModelResponse:
    return ModelResponse(
        content=[{"type": "text", "text": text}],
        stop_reason="end_turn",
        model="scripted",
        usage=usage or Usage(),
    )


def tool_turn(*calls: tuple[str, dict[str, Any]], text: str = "") -> ModelResponse:
    """An assistant turn requesting one or more tool calls."""
    content: list[JSON] = [{"type": "text", "text": text}] if text else []
    for name, args in calls:
        content.append(
            {"type": "tool_use", "id": f"toolu_{next(_ids):04d}", "name": name, "input": args}
        )
    return ModelResponse(content=content, stop_reason="tool_use", model="scripted")


class ScriptedProvider:
    """Returns the queued responses in order and records every request.

    A queued :class:`ProviderError` is raised instead, to script outages.
    """

    model = "scripted"

    def __init__(self, responses: Iterable[ModelResponse | ProviderError]) -> None:
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._responses:
            raise ProviderError("scripted provider ran out of responses")
        response = self._responses.pop(0)
        if isinstance(response, ProviderError):
            raise response
        return response
