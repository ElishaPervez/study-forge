"""Assemble one streamed OpenRouter reply.

Visible text is delivered while it arrives, while the complete reply - text,
tool calls, usage, and completion reason - is preserved for the caller. A reply
that ends without a terminal indication is not a successful reply.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

DONE_MARKER = "[DONE]"


class StreamError(ValueError):
    """The streamed output could not be interpreted."""


@dataclass
class ToolCallFragment:
    index: int
    call_id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class StreamedReply:
    text: str = ""
    tool_calls: list[ToolCallFragment] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    finish_reason: str = ""
    reasoning: str = ""
    terminal: bool = False


class StreamAccumulator:
    """Feed raw event lines in; read the assembled reply out."""

    def __init__(
        self,
        *,
        on_text: Callable[[str], None] | None = None,
        on_event: Callable[[str], None] | None = None,
    ) -> None:
        self._on_text = on_text
        # Timing instrumentation observes the shape of the stream: when lines
        # arrive and what kind they were, so a slow reply can be explained.
        self._on_event = on_event
        self._text_parts: list[str] = []
        self._reasoning_parts: list[str] = []
        self._tool_calls: dict[int, ToolCallFragment] = {}
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.reasoning_tokens = 0
        self.finish_reason = ""
        self.done = False
        self.error: str | None = None

    @property
    def terminal(self) -> bool:
        return self.done or bool(self.finish_reason)

    def feed_line(self, line: str) -> None:
        if line.startswith(":"):
            # A keepalive comment is not output.
            if self._on_event is not None:
                self._on_event("keepalive")
            return
        stripped = line.strip()
        if not stripped or not stripped.startswith("data:"):
            return
        data = stripped[len("data:") :].strip()
        if not data:
            return
        if self._on_event is not None:
            self._on_event("line")
        if data == DONE_MARKER:
            self.done = True
            if self._on_event is not None:
                self._on_event("done")
            return
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as error:
            raise StreamError("malformed event data") from error
        self.feed_payload(payload)

    def feed_payload(self, payload: object) -> None:
        if not isinstance(payload, dict):
            raise StreamError("malformed event payload")
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            self.error = str(message) if message else "the model provider reported an error"
            if self._on_event is not None:
                self._on_event("error")
            return
        if payload.get("usage") is not None and self._on_event is not None:
            self._on_event("usage")
        self._read_usage(payload.get("usage"))
        choices = payload.get("choices")
        if choices is None:
            return
        if not isinstance(choices, list):
            raise StreamError("malformed event choices")
        if not choices:
            # A usage-only message carries no output.
            return
        choice = choices[0]
        if not isinstance(choice, dict):
            raise StreamError("malformed event choice")
        finish_reason = choice.get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason:
            self.finish_reason = finish_reason
        delta = choice.get("delta")
        if delta is None:
            delta = choice.get("message")
        if delta is None:
            return
        if not isinstance(delta, dict):
            raise StreamError("malformed event delta")
        self._read_reasoning(delta.get("reasoning"))
        self._read_content(delta.get("content"))
        self._read_tool_calls(delta.get("tool_calls"))

    def result(self) -> StreamedReply:
        return StreamedReply(
            text="".join(self._text_parts),
            tool_calls=[self._tool_calls[index] for index in sorted(self._tool_calls)],
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            reasoning_tokens=self.reasoning_tokens,
            finish_reason=self.finish_reason,
            reasoning="".join(self._reasoning_parts),
            terminal=self.terminal,
        )

    def _read_usage(self, usage: object) -> None:
        if not isinstance(usage, dict):
            return
        self.prompt_tokens = _token_count(usage.get("prompt_tokens"), self.prompt_tokens)
        self.completion_tokens = _token_count(
            usage.get("completion_tokens"), self.completion_tokens
        )
        details = usage.get("completion_tokens_details")
        if isinstance(details, dict):
            self.reasoning_tokens = _token_count(
                details.get("reasoning_tokens"), self.reasoning_tokens
            )

    def _read_reasoning(self, reasoning: object) -> None:
        # Hidden reasoning is never counted as generated HTML.
        if isinstance(reasoning, str) and reasoning:
            self._reasoning_parts.append(reasoning)
            if self._on_event is not None:
                self._on_event("reasoning")

    def _read_content(self, content: object) -> None:
        if isinstance(content, str) and content:
            self._text_parts.append(content)
            if self._on_text is not None:
                self._on_text(content)
            if self._on_event is not None:
                self._on_event("text")

    def _read_tool_calls(self, tool_calls: object) -> None:
        if tool_calls is None:
            return
        if not isinstance(tool_calls, list):
            raise StreamError("malformed event tool calls")
        if self._on_event is not None:
            self._on_event("tool_call")
        for entry in tool_calls:
            if not isinstance(entry, dict):
                raise StreamError("malformed event tool call")
            index = entry.get("index")
            if isinstance(index, bool) or not isinstance(index, int):
                index = len(self._tool_calls)
            fragment = self._tool_calls.setdefault(index, ToolCallFragment(index))
            call_id = entry.get("id")
            if isinstance(call_id, str) and call_id and not fragment.call_id:
                fragment.call_id = call_id
            function = entry.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if isinstance(name, str) and name:
                fragment.name += name
            arguments = function.get("arguments")
            if isinstance(arguments, str) and arguments:
                fragment.arguments += arguments


def _token_count(value: object, current: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return current
    return value
