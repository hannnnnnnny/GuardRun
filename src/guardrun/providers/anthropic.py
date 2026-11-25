"""Claude via the official Anthropic Python SDK."""

from __future__ import annotations

from typing import Any

import anthropic

from guardrun.errors import ProviderError
from guardrun.messages import JSON, ModelResponse
from guardrun.providers.base import ModelRequest
from guardrun.usage import Usage

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_MAX_TOKENS = 64_000
DEFAULT_EFFORT = "high"
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
# Server-side fallback lets a policy-declined turn be re-run on a fallback
# model inside the same call, routed by refusal category.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _usage(raw: Any) -> Usage:
    if raw is None:
        return Usage()
    return Usage(
        input_tokens=raw.input_tokens or 0,
        output_tokens=raw.output_tokens or 0,
        cache_read_input_tokens=raw.cache_read_input_tokens or 0,
        cache_creation_input_tokens=raw.cache_creation_input_tokens or 0,
    )


def to_model_response(message: Any) -> ModelResponse:
    """Convert an SDK message, keeping every block (thinking included) verbatim."""
    content: list[JSON] = [
        block.model_dump(mode="json", exclude_none=True) for block in message.content
    ]
    return ModelResponse(
        content=content,
        stop_reason=message.stop_reason,
        model=message.model,
        usage=_usage(message.usage),
    )


class AnthropicProvider:
    """Streams each turn (large ``max_tokens`` would otherwise risk HTTP timeouts)
    and returns the final assembled message.
    """

    service_name = "Anthropic"
    auth_hint = "set ANTHROPIC_API_KEY or run `ant auth login`"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        effort: str = DEFAULT_EFFORT,
        fallbacks: bool = True,
        client: Any = None,
    ) -> None:
        if effort not in EFFORT_LEVELS:
            raise ValueError(f"effort must be one of {EFFORT_LEVELS}")
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self.fallbacks = fallbacks
        # Credentials resolve from the environment or an `ant auth login` profile.
        self.client = client if client is not None else anthropic.Anthropic()

    def build_params(self, request: ModelRequest) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": request.system,
            "messages": request.messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort},
            # Caches the stable prefix (tools, system, earlier turns) across calls.
            "cache_control": {"type": "ephemeral"},
        }
        if request.tools:
            # Stream tool inputs as generated; the executor validates every input.
            params["tools"] = [{**tool, "eager_input_streaming": True} for tool in request.tools]
        if self.fallbacks:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    def complete(self, request: ModelRequest) -> ModelResponse:
        params = self.build_params(request)
        try:
            with self._stream(params) as stream:
                message = stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise ProviderError(f"authentication failed: {self.auth_hint}") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError(
                "rate limited by the API after retries; try again later", transient=True
            ) from exc
        except anthropic.BadRequestError as exc:
            raise ProviderError(f"the API rejected the request: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            # 5xx and 529 (overloaded) are the service's problem and usually pass.
            raise ProviderError(
                f"API error {exc.status_code}: {exc.message}", transient=exc.status_code >= 500
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(
                f"could not reach the {self.service_name} API", transient=True
            ) from exc
        return to_model_response(message)

    def _stream(self, params: dict[str, Any]) -> Any:
        # The beta namespace carries the server-side fallback parameters.
        return self.client.beta.messages.stream(**params)
