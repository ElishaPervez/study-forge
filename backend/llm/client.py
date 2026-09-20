from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field
from threading import Event
from typing import Protocol

import httpx

from backend.diagnostics.trace import Trace, current_trace
from backend.ingest.pdf import InputImage
from backend.llm.stream import StreamAccumulator, StreamedReply, StreamError

API_URL = "https://openrouter.ai/api/v1/chat/completions"
RETRYABLE = {408, 429, 500, 502, 503, 504}
# A gap in the stream longer than this is worth naming: it is provider stall time,
# not generation time, and it is invisible in a total-duration figure.
STALL_GAP_MS = 1000.0
# Long calls are reported as they go, so a five-minute reply shows whether the
# model is reasoning or writing while it is still running.
CHECKPOINT_SECONDS = 15.0


def _payload_chars(messages: Sequence[dict]) -> int:
    """The request body's size, measured without serializing it again."""
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str):
                    total += len(text)
                image = part.get("image_url")
                if isinstance(image, dict):
                    url = image.get("url")
                    if isinstance(url, str):
                        total += len(url)
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            total += len(json.dumps(tool_calls, default=str))
    return total


class StreamTimer:
    """Wall-clock shape of one streamed model call.

    It answers the questions a total duration cannot: how long the request took
    to be acknowledged (time to first byte), how long until the model produced
    its first token, whether output came in a steady stream or in bursts with
    provider stalls between them, and how fast tokens arrived once they started.
    """

    def __init__(self, *, checkpoint_seconds: float = CHECKPOINT_SECONDS) -> None:
        self._checkpoint_seconds = checkpoint_seconds
        self.started = time.perf_counter()
        self.status: int | None = None
        self.headers_ms: float | None = None
        self.first_reasoning_ms: float | None = None
        self.first_text_ms: float | None = None
        self.first_tool_ms: float | None = None
        self.first_any_ms: float | None = None
        self.finished_ms: float | None = None
        self.lines = 0
        self.keepalives = 0
        self.events = 0
        self.text_events = 0
        self.reasoning_events = 0
        self.tool_fragments = 0
        self.text_chars = 0
        self.reasoning_chars = 0
        self.gaps_ms: list[float] = []
        self.stall_ms = 0.0
        self.longest_gap_ms = 0.0
        self.longest_gap_at_ms = 0.0
        self.checkpoints: list[dict[str, object]] = []
        self._last_event_at: float | None = None
        self._checkpoint_at = self.started

    def _ms(self, since: float) -> float:
        return round((time.perf_counter() - since) * 1000.0, 1)

    def sent(self) -> None:
        """The request is leaving: start the clock to first byte."""
        self.started = time.perf_counter()
        self._checkpoint_at = self.started
        self._last_event_at = self.started

    def headers(self, status: int) -> None:
        self.status = status
        self.headers_ms = self._ms(self.started)
        # Stream gaps are measured from the first byte, not from the send: waiting
        # for the provider to answer is already reported as time to first byte.
        self._last_event_at = time.perf_counter()

    def note(self, kind: str) -> None:
        """One piece of the stream arrived; record when and which kind."""
        now = time.perf_counter()
        self.events += 1
        offset = (now - self.started) * 1000.0
        if self._last_event_at is not None:
            gap = (now - self._last_event_at) * 1000.0
            if gap > self.longest_gap_ms:
                self.longest_gap_ms = round(gap, 1)
                self.longest_gap_at_ms = round(offset, 1)
            if gap >= STALL_GAP_MS:
                self.gaps_ms.append(round(gap, 1))
                self.stall_ms = round(self.stall_ms + gap, 1)
        self._last_event_at = now
        if kind == "line":
            self.lines += 1
        elif kind == "keepalive":
            self.keepalives += 1
        elif kind in {"text", "reasoning", "tool_call"}:
            if self.first_any_ms is None:
                self.first_any_ms = round(offset, 1)
            if kind == "text":
                self.text_events += 1
                if self.first_text_ms is None:
                    self.first_text_ms = round(offset, 1)
            elif kind == "reasoning":
                self.reasoning_events += 1
                if self.first_reasoning_ms is None:
                    self.first_reasoning_ms = round(offset, 1)
            else:
                self.tool_fragments += 1
                if self.first_tool_ms is None:
                    self.first_tool_ms = round(offset, 1)
        if now - self._checkpoint_at >= self._checkpoint_seconds:
            self._checkpoint_at = now
            self.checkpoints.append(
                {
                    "t_ms": round(offset, 1),
                    "text_chars": self.text_chars,
                    "reasoning_chars": self.reasoning_chars,
                    "events": self.events,
                }
            )

    def observe_reply(self, reply: StreamedReply) -> None:
        """Fold the assembled reply's visible size into the timing record."""
        self.text_chars = len(reply.text)
        self.reasoning_chars = len(reply.reasoning)

    def failed(self) -> None:
        """The call ended without a reply; keep the numbers collected so far."""
        self.finished_ms = self._ms(self.started)

    def total_ms(self) -> float:
        """Wall time of the call, frozen once it is known."""
        if self.finished_ms is None:
            self.finished_ms = self._ms(self.started)
        return self.finished_ms

    def fields(self) -> dict[str, object]:
        """Everything measured, ready for one trace span."""
        stream_ms = round(self.total_ms() - (self.headers_ms or 0.0), 1)
        fields: dict[str, object] = {
            "status": self.status,
            "ttfb_ms": self.headers_ms,
            "stream_ms": stream_ms,
            "first_token_ms": self.first_text_ms,
            "first_reasoning_ms": self.first_reasoning_ms,
            "first_tool_ms": self.first_tool_ms,
            "first_any_ms": self.first_any_ms,
            "lines": self.lines,
            "keepalives": self.keepalives,
            "events": self.events,
            "text_events": self.text_events,
            "reasoning_events": self.reasoning_events,
            "tool_fragments": self.tool_fragments,
            "text_chars": self.text_chars or None,
            "reasoning_chars": self.reasoning_chars or None,
            "stalls": len(self.gaps_ms) or None,
            "stall_ms": self.stall_ms or None,
            "gaps_ms": self.gaps_ms or None,
            "longest_gap_ms": self.longest_gap_ms or None,
            "longest_gap_at_ms": self.longest_gap_at_ms or None,
            "checkpoints": self.checkpoints or None,
        }
        return fields

    def token_fields(self, reply: StreamedReply) -> dict[str, object]:
        """Token counts and rates, once the reply is complete."""
        seconds = max(self.total_ms() / 1000.0, 0.001)
        return {
            "prompt_tokens": reply.prompt_tokens,
            "completion_tokens": reply.completion_tokens,
            "reasoning_tokens": reply.reasoning_tokens,
            "finish_reason": reply.finish_reason,
            "terminal": reply.terminal,
            "tokens_per_second": round(reply.completion_tokens / seconds, 1),
            "text_chars_per_second": round(len(reply.text) / seconds, 1),
        }


def _log_call_done(trace: Trace | None, timer: StreamTimer, reply: StreamedReply) -> None:
    if trace is None:
        return
    trace.event(
        "llm.http.done",
        ttfb_ms=timer.headers_ms,
        first_token_ms=timer.first_text_ms,
        first_any_ms=timer.first_any_ms,
        total_ms=timer.total_ms(),
        completion_tokens=reply.completion_tokens,
        reasoning_tokens=reply.reasoning_tokens,
        finish_reason=reply.finish_reason or None,
        stalls=len(timer.gaps_ms) or None,
        longest_gap_ms=timer.longest_gap_ms or None,
    )


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
        trace = current_trace()
        timer = StreamTimer()
        # The span is opened around the whole call so a transport failure is
        # measured too, and the numbers collected so far are still reported.
        span_context = (
            trace.span(
                "llm.http",
                model=self._model,
                provider=self._provider_only[0] if self._provider_only else None,
                payload_chars=_payload_chars(messages),
                max_tokens=self._max_output_tokens,
                reasoning_effort=self._reasoning_effort,
                tools=len(tools) if tools else None,
            )
            if trace is not None
            else nullcontext({})
        )
        with span_context as extra:
            accumulator = StreamAccumulator(on_text=on_text, on_event=timer.note)
            try:
                timer.sent()
                with self._client.stream("POST", API_URL, json=body) as response:
                    timer.headers(response.status_code)
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
                if accumulator.error is not None:
                    raise LLMError(
                        f"OpenRouter reported an error after output began: {accumulator.error}",
                        retryable=True,
                    )
                if not accumulator.terminal:
                    raise LLMError(
                        "OpenRouter ended the reply before it finished", retryable=True
                    )
                reply_stream = accumulator.result()
                timer.observe_reply(reply_stream)
                extra.update(timer.token_fields(reply_stream))
                _log_call_done(trace, timer, reply_stream)
            except httpx.TimeoutException as exc:
                timer.failed()
                raise LLMError("OpenRouter request timed out", retryable=True) from exc
            except httpx.TransportError as exc:
                timer.failed()
                raise LLMError(f"OpenRouter request failed: {exc}", retryable=True) from exc
            except StreamError as error:
                timer.failed()
                raise LLMError(
                    f"OpenRouter sent unreadable streamed output: {error}", retryable=True
                ) from error
            except LLMError:
                # Includes a stopped call: nothing was produced, so freeze the clock.
                timer.failed()
                raise
            finally:
                # Whatever happened, the numbers collected so far belong in the trace.
                extra.update(timer.fields())
        calls = [
            ToolCall(
                id=fragment.call_id,
                name=fragment.name,
                arguments=fragment.arguments or "{}",
            )
            for fragment in reply_stream.tool_calls
        ]
        return LLMReply(
            text=reply_stream.text,
            tool_calls=calls,
            prompt_tokens=reply_stream.prompt_tokens,
            completion_tokens=reply_stream.completion_tokens,
            finish_reason=reply_stream.finish_reason,
            reasoning_tokens=reply_stream.reasoning_tokens,
        )
