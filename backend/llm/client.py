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
        transport: httpx.BaseTransport | None = None,
        timeout: float = 600.0,
    ) -> None:
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._max_output_tokens = max_output_tokens
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
                "only": ["deepseek"],
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
        message = payload["choices"][0].get("message", {})
        usage = payload.get("usage", {})
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
        )
