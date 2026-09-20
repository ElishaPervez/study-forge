import base64
import json
import threading
from pathlib import Path

import httpx
import pytest

from backend.ingest.pdf import InputImage, PageImage
from backend.llm.client import LLMError, OpenRouterLLM, image_part


def _client(handler, *, provider_only=None) -> OpenRouterLLM:
    return OpenRouterLLM(
        api_key="sk-test",
        model="deepseek/deepseek-v4.1-flash",
        reasoning_effort="high",
        max_output_tokens=200000,
        provider_only=provider_only,
        transport=httpx.MockTransport(handler),
    )


def sse_body(*payloads: object, done: bool = True, ensure_ascii: bool = True) -> str:
    lines = [
        f"data: {json.dumps(payload, ensure_ascii=ensure_ascii)}\n\n" for payload in payloads
    ]
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines)


def sse_response(*payloads: object, done: bool = True) -> httpx.Response:
    return httpx.Response(
        200,
        text=sse_body(*payloads, done=done),
        headers={"content-type": "text/event-stream"},
    )


def content_delta(content: str, **extra: object) -> dict:
    return {"choices": [{"delta": {"content": content}, **extra}]}


class ScriptedStream(httpx.SyncByteStream):
    """A reply body delivered in the exact chunks a test chooses."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    def __iter__(self):
        yield from self.chunks


class GatedStream(httpx.SyncByteStream):
    """A reply body that stops until the test releases the rest."""

    def __init__(self, first: bytes, rest: bytes, gate: threading.Event) -> None:
        self.first = first
        self.rest = rest
        self.gate = gate

    def __iter__(self):
        yield self.first
        if not self.gate.wait(timeout=5):
            raise RuntimeError("test never released the rest of the reply")
        yield self.rest


def test_request_pins_model_params_and_carries_no_remote_url() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return sse_response(
            content_delta("<html></html>", finish_reason="stop"),
            {
                "choices": [],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            },
        )

    reply = _client(handler).complete([{"role": "user", "content": "hi"}])

    assert captured["model"] == "deepseek/deepseek-v4.1-flash"
    assert captured["max_tokens"] == 200000
    assert captured["reasoning_effort"] == "high"
    assert captured["stream"] is True
    assert captured["provider"] == {
        "only": ["deepseek"],
        "allow_fallbacks": False,
    }
    assert captured["messages"] == [{"role": "user", "content": "hi"}]
    assert "tools" not in captured
    assert reply.text == "<html></html>"
    assert reply.prompt_tokens == 11
    assert reply.completion_tokens == 7
    assert reply.tool_calls == []
    assert reply.finish_reason == "stop"


def test_provider_follows_the_model_namespace() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return sse_response(content_delta("<html></html>", finish_reason="stop"))

    _client(handler).complete([{"role": "user", "content": "hi"}])

    assert captured["provider"] == {"only": ["deepseek"], "allow_fallbacks": False}


def test_provider_can_be_pinned_explicitly() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return sse_response(content_delta("<html></html>", finish_reason="stop"))

    _client(handler, provider_only=["deepseek"]).complete(
        [{"role": "user", "content": "hi"}]
    )

    assert captured["provider"] == {"only": ["deepseek"], "allow_fallbacks": False}


def test_text_is_observable_while_the_reply_is_still_open() -> None:
    gate = threading.Event()
    first_seen = threading.Event()
    pieces: list[str] = []
    first = sse_body(content_delta("<html><body>first part")).encode("utf-8")
    rest = sse_body(
        content_delta(" and the rest</body></html>", finish_reason="stop")
    ).encode("utf-8")
    payloads = {"first": first, "rest": rest}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=GatedStream(payloads["first"], payloads["rest"], gate),
            headers={"content-type": "text/event-stream"},
        )

    def on_text(piece: str) -> None:
        pieces.append(piece)
        first_seen.set()

    collected: list[object] = []
    worker = threading.Thread(
        target=lambda: collected.append(
            _client(handler).complete([{"role": "user", "content": "hi"}], on_text=on_text)
        )
    )
    worker.start()
    try:
        assert first_seen.wait(timeout=3)
        # The observer has the first part while the reply is still unfinished.
        assert gate.is_set() is False
        assert pieces == ["<html><body>first part"]
    finally:
        gate.set()
        worker.join(timeout=5)

    reply = collected[0]
    assert reply.text == "<html><body>first part and the rest</body></html>"
    assert pieces == ["<html><body>first part", " and the rest</body></html>"]
    assert reply.finish_reason == "stop"


def test_a_reply_split_across_network_reads_is_assembled_once() -> None:
    body = sse_body(
        content_delta("<html><body>"),
        content_delta("split across reads</body></html>", finish_reason="stop"),
    ).encode("utf-8")
    pieces = [
        body[: 12],
        body[12:40],
        body[40:41],
        body[41:],
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=ScriptedStream(pieces),
            headers={"content-type": "text/event-stream"},
        )

    reply = _client(handler).complete([{"role": "user", "content": "hi"}])

    assert reply.text == "<html><body>split across reads</body></html>"


def test_utf8_characters_split_across_reads_survive() -> None:
    body = sse_body(
        content_delta("<p>café — 30 °C, ½ litre</p>", finish_reason="stop"),
        ensure_ascii=False,
    ).encode("utf-8")
    em_dash = body.index("—".encode()) + 1
    quote = body.index("½".encode()) + 1
    pieces = [body[:em_dash], body[em_dash:quote], body[quote:]]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=ScriptedStream(pieces),
            headers={"content-type": "text/event-stream"},
        )

    reply = _client(handler).complete([{"role": "user", "content": "hi"}])

    assert reply.text == "<p>café — 30 °C, ½ litre</p>"


def test_blank_lines_and_keepalives_do_not_disturb_the_reply() -> None:
    body = (
        ": OPENROUTER PROCESSING\n\n"
        + f"data: {json.dumps(content_delta('<html></html>', finish_reason='stop'))}\n\n"
        "\n"
        ": keepalive\n"
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=body,
            headers={"content-type": "text/event-stream"},
        )

    reply = _client(handler).complete([{"role": "user", "content": "hi"}])

    assert reply.text == "<html></html>"


def test_reasoning_starved_completion_is_surfaced_for_diagnostics() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return sse_response(
            {"choices": [{"delta": {"content": None, "reasoning": "thinking"}}]},
            {"choices": [{"delta": {}, "finish_reason": "length"}]},
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 13833,
                    "completion_tokens": 32768,
                    "completion_tokens_details": {"reasoning_tokens": 32768},
                },
            },
        )

    reply = _client(handler).complete([{"role": "user", "content": "hi"}])

    assert reply.text == ""
    assert reply.finish_reason == "length"
    assert reply.completion_tokens == 32768
    assert reply.reasoning_tokens == 32768


def test_tool_calls_are_surfaced(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return sse_response(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "read_ref", "arguments": '{"name":"type-'},
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {
                                        "name": "erence",
                                        "arguments": 'process.md"}',
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    reply = _client(handler).complete(
        [{"role": "user", "content": "hi"}], tools=[{"type": "function"}]
    )

    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0].id == "call_1"
    assert reply.tool_calls[0].name == "read_reference"
    assert reply.tool_calls[0].arguments == '{"name":"type-process.md"}'
    assert reply.prompt_tokens == 3
    assert reply.completion_tokens == 2


def test_a_call_without_an_observer_is_supported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return sse_response(content_delta("<html></html>", finish_reason="stop"))

    reply = _client(handler).complete([{"role": "user", "content": "hi"}])

    assert reply.text == "<html></html>"


def test_a_stopped_call_closes_the_response_without_a_reply() -> None:
    closed: list[bool] = []
    stop = threading.Event()
    stop.set()

    class TrackingStream(httpx.SyncByteStream):
        def __iter__(self):
            yield sse_body(content_delta("never delivered")).encode("utf-8")

        def close(self) -> None:
            closed.append(True)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=TrackingStream(),
            headers={"content-type": "text/event-stream"},
        )

    with pytest.raises(LLMError) as caught:
        _client(handler).complete([{"role": "user", "content": "hi"}], stop=stop)

    assert caught.value.retryable is False
    assert "stopped" in str(caught.value).lower()
    assert closed == [True]


def test_rate_limit_is_a_retryable_error() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"error": {"message": "slow down"}})

    with pytest.raises(LLMError) as caught:
        _client(handler).complete([{"role": "user", "content": "hi"}])

    assert caught.value.retryable is True
    assert "429" in str(caught.value) or "rate" in str(caught.value).lower()
    assert calls == 1


@pytest.mark.parametrize("status", [408, 500, 502, 503, 504])
def test_other_transient_http_failures_are_retryable_without_client_retry(status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"error": {"message": "temporary"}})

    with pytest.raises(LLMError) as caught:
        _client(handler).complete([{"role": "user", "content": "hi"}])

    assert caught.value.retryable is True
    assert str(status) in str(caught.value)
    assert calls == 1


def test_timeout_is_retryable_without_client_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(LLMError) as caught:
        _client(handler).complete([{"role": "user", "content": "hi"}])

    assert caught.value.retryable is True
    assert "timed out" in str(caught.value).lower()
    assert calls == 1


def test_auth_failure_is_not_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid key"}})

    with pytest.raises(LLMError) as caught:
        _client(handler).complete([{"role": "user", "content": "hi"}])

    assert caught.value.retryable is False


def test_a_connection_that_ends_without_a_terminal_indication_is_not_a_reply() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return sse_response(content_delta("<html><body>half a guide"), done=False)

    with pytest.raises(LLMError) as caught:
        _client(handler).complete([{"role": "user", "content": "hi"}])

    assert caught.value.retryable is True
    assert "before it finished" in str(caught.value)


def test_a_broken_connection_mid_reply_is_not_a_reply() -> None:
    body = sse_body(content_delta("<html><body>cut off")).encode("utf-8")

    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield body[:20]
            raise httpx.ReadError("connection reset")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=BrokenStream(),
            headers={"content-type": "text/event-stream"},
        )

    with pytest.raises(LLMError) as caught:
        _client(handler).complete([{"role": "user", "content": "hi"}])

    assert caught.value.retryable is True
    assert "connection reset" in str(caught.value)


def test_an_error_sent_after_output_began_is_not_a_successful_reply() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return sse_response(
            content_delta("<html><body>start"),
            {"error": {"message": "provider failed mid-reply"}},
        )

    with pytest.raises(LLMError) as caught:
        _client(handler).complete([{"role": "user", "content": "hi"}])

    assert caught.value.retryable is True
    assert "provider failed mid-reply" in str(caught.value)


def test_malformed_event_data_is_not_a_successful_reply() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='data: {"choices": [{"delta": {"content": "ok"}}]}\n\ndata: {truncated\n\n',
            headers={"content-type": "text/event-stream"},
        )

    with pytest.raises(LLMError) as caught:
        _client(handler).complete([{"role": "user", "content": "hi"}])

    assert caught.value.retryable is True
    assert "unreadable" in str(caught.value).lower()


def test_image_part_is_a_base64_jpeg(tmp_path: Path) -> None:
    path = tmp_path / "0001.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0jpegbytes")

    part = image_part(PageImage(1, path, 100, 140))

    assert part["type"] == "image_url"
    assert part["image_url"]["url"].startswith("data:image/jpeg;base64,")
    encoded = part["image_url"]["url"].split(",", 1)[1]
    assert base64.b64decode(encoded) == b"\xff\xd8\xff\xe0jpegbytes"


def test_image_part_uses_the_input_media_type_and_original_bytes(tmp_path: Path) -> None:
    path = tmp_path / "source.png"
    path.write_bytes(b"original png bytes")

    part = image_part(InputImage(1, path, "image/png", "Source image 1"))

    assert part["image_url"]["url"].startswith("data:image/png;base64,")
    encoded = part["image_url"]["url"].split(",", 1)[1]
    assert base64.b64decode(encoded) == b"original png bytes"
