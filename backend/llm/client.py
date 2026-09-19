from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from backend.ingest.pdf import InputImage

API_URL = "https://openrouter.ai/api/v1/chat/completions"
RETRYABLE = {408, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class LLMReply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # A reasoning model can spend the whole output budget on hidden reasoning and
    # return no text at all; the caller needs these to explain that instead of
    # blaming the artifact it never received.
    finish_reason: str = ""
    reasoning_tokens: int = 0


class LLM(Protocol):
    def complete(
        self, messages: Sequence[dict], tools: Sequence[dict] | None = None
    ) -> LLMReply: ...


def image_part(image: InputImage) -> dict:
    payload = base64.b64encode(image.path.read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{image.media_type};base64,{payload}"},
    }


class OpenRouterLLM:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        reasoning_effort: str,
        max_output_tokens: int,
        provider_only: Sequence[str] | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 600.0,
    ) -> None:
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._max_output_tokens = max_output_tokens
        # The product pins one model and forbids provider fallback, so the pinned
        # provider is derived from the model's namespace. Keeping it as a second
        # hardcoded constant let it go stale silently on a model swap.
        self._provider_only = (
            list(provider_only) if provider_only else [model.split("/", 1)[0]]
        )
        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    def complete(
        self, messages: Sequence[dict], tools: Sequence[dict] | None = None
    ) -> LLMReply:
        body: dict = {
            "model": self._model,
            "messages": list(messages),
            "max_tokens": self._max_output_tokens,
            "reasoning_effort": self._reasoning_effort,
            "provider": {
                "only": self._provider_only,
                "allow_fallbacks": False,
            },
        }
        if tools:
            body["tools"] = list(tools)
        try:
            response = self._client.post(API_URL, json=body)
        except httpx.TimeoutException as exc:
            raise LLMError("OpenRouter request timed out", retryable=True) from exc
        except httpx.TransportError as exc:
            raise LLMError(f"OpenRouter request failed: {exc}", retryable=True) from exc

        if response.status_code != 200:
            raise LLMError(
                f"OpenRouter returned {response.status_code}: {response.text[:300]}",
                retryable=response.status_code in RETRYABLE,
            )
        payload = response.json()
        if not payload.get("choices"):
            raise LLMError("OpenRouter returned no choices", retryable=False)
        choice = payload["choices"][0]
        message = choice.get("message", {})
        usage = payload.get("usage", {})
        token_details = usage.get("completion_tokens_details") or {}
        calls = [
            ToolCall(
                id=call.get("id", ""),
                name=call.get("function", {}).get("name", ""),
                arguments=call.get("function", {}).get("arguments", "{}"),
            )
            for call in message.get("tool_calls") or []
        ]
        return LLMReply(
            text=message.get("content") or "",
            tool_calls=calls,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            finish_reason=str(choice.get("finish_reason") or ""),
            reasoning_tokens=int(token_details.get("reasoning_tokens", 0) or 0),
        )
