import base64
import json
from pathlib import Path

import httpx
import pytest

from backend.ingest.pdf import InputImage, PageImage
from backend.llm.client import LLMError, OpenRouterLLM, image_part


def _client(handler) -> OpenRouterLLM:
    return OpenRouterLLM(
        api_key="sk-test",
        model="deepseek/deepseek-v4.1-flash",
        reasoning_effort="low",
        max_output_tokens=32768,
        transport=httpx.MockTransport(handler),
    )


def test_request_pins_model_params_and_carries_no_remote_url() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "<html></html>"}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            },
        )

    reply = _client(handler).complete([{"role": "user", "content": "hi"}])

    assert captured["model"] == "deepseek/deepseek-v4.1-flash"
    assert captured["max_tokens"] == 32768
    assert captured["reasoning_effort"] == "low"
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


def test_tool_calls_are_surfaced(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_reference",
                                        "arguments": '{"name":"type-process.md"}',
                                    },
                                }
                            ],
                        }
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
