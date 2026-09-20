from __future__ import annotations

import base64
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from threading import Event
from typing import Protocol

import httpx

from backend.ingest.pdf import InputImage
from backend.llm.stream import StreamAccumulator, StreamError

API_URL = "https://openrouter.ai/api/v1/chat/completions"
RETRYABLE = {408, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMStopped(LLMError):
    """The caller stopped the work, so the reply is not a usable result."""

    def __init__(self, message: str = "model work was stopped") -> None:
        super().__init__(message, retryable=False)


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
        self,
        messages: Sequence[dict],
        tools: Sequence[dict] | None = None,
        *,
        on_text: Callable[[str], None] | None = None,
        stop: Event | None = None,
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
        self,
        messages: Sequence[dict],
        tools: Sequence[dict] | None = None,
        *,
        on_text: Callable[[str], None] | None = None,
        stop: Event | None = None,
    ) -> LLMReply:
        body: dict = {
            "model": self._model,
            "messages": list(messages),
            "max_tokens": self._max_output_tokens,
            "reasoning_effort": self._reasoning_effort,
            "stream": True,
            "provider": {
                "only": self._provider_only,
                "allow_fallbacks": False,
            },
        }
        if tools:
            body["tools"] = list(tools)
        accumulator = StreamAccumulator(on_text=on_text)
        try:
            with self._client.stream("POST", API_URL, json=body) as response:
                if response.status_code != 200:
                    response.read()
                    raise LLMError(
                        f"OpenRouter returned {response.status_code}: {response.text[:300]}",
                        retryable=response.status_code in RETRYABLE,
                    )
                for line in response.iter_lines():
                    if stop is not None and stop.is_set():
                        raise LLMStopped()
                    accumulator.feed_line(line)
        except httpx.TimeoutException as exc:
            raise LLMError("OpenRouter request timed out", retryable=True) from exc
        except httpx.TransportError as exc:
            raise LLMError(f"OpenRouter request failed: {exc}", retryable=True) from exc
        except StreamError as error:
            raise LLMError(
                f"OpenRouter sent unreadable streamed output: {error}", retryable=True
            ) from error

        if accumulator.error is not None:
            raise LLMError(
                f"OpenRouter reported an error after output began: {accumulator.error}",
                retryable=True,
            )
        if not accumulator.terminal:
            raise LLMError(
                "OpenRouter ended the reply before it finished", retryable=True
            )
        reply = accumulator.result()
        calls = [
            ToolCall(
                id=fragment.call_id,
                name=fragment.name,
                arguments=fragment.arguments or "{}",
            )
            for fragment in reply.tool_calls
        ]
        return LLMReply(
            text=reply.text,
            tool_calls=calls,
            prompt_tokens=reply.prompt_tokens,
            completion_tokens=reply.completion_tokens,
            finish_reason=reply.finish_reason,
            reasoning_tokens=reply.reasoning_tokens,
        )
