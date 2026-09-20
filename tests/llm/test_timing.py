import json
from pathlib import Path

import httpx
import pytest

from backend.diagnostics.trace import Trace, logs_dir_for, use_trace
from backend.llm.client import LLMError, OpenRouterLLM, StreamTimer
from backend.llm.stream import StreamedReply


def sse(*payloads: object) -> str:
    body = "".join(f"data: {json.dumps(payload)}\n\n" for payload in payloads)
    return body + "data: [DONE]\n\n"


def client(handler) -> OpenRouterLLM:
    return OpenRouterLLM(
        api_key="sk-test",
        model="deepseek/deepseek-v4.1-flash",
        reasoning_effort="high",
        max_output_tokens=200000,
        transport=httpx.MockTransport(handler),
    )


def events_by_name(path: Path) -> dict[str, dict]:
    return {
        event["event"]: event
        for event in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    }


def test_a_streamed_call_reports_acknowledgement_first_token_and_tokens(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=sse(
                {"choices": [{"delta": {"reasoning": "thinking"}}]},
                {"choices": [{"delta": {"content": "<html>"}}]},
                {"choices": [{"delta": {"content": "</html>"}, "finish_reason": "stop"}]},
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 4,
                        "completion_tokens_details": {"reasoning_tokens": 1},
                    },
                },
            ),
            headers={"content-type": "text/event-stream"},
        )

    trace = Trace("f" * 32, logs_dir_for(tmp_path))
    with use_trace(trace):
        reply = client(handler).complete([{"role": "user", "content": "hi"}])
    trace.finish(ok=True, state="completed")

    assert reply.text == "<html></html>"
    events = events_by_name(trace.path)
    call = events["llm.http"]
    assert call["ttfb_ms"] is not None
    assert call["first_token_ms"] >= call["ttfb_ms"]
    assert call["first_reasoning_ms"] is not None
    assert call["completion_tokens"] == 4
    assert call["reasoning_tokens"] == 1
    assert call["finish_reason"] == "stop"
    assert call["tokens_per_second"] > 0
    assert call["payload_chars"] > 0
    # The milestone event and the summary line agree with the measured span.
    assert events["llm.http.done"]["first_token_ms"] == call["first_token_ms"]
    assert events["trace.summary"]["model_calls"] == 1


def test_a_failed_call_is_still_timed(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "slow down"}})

    trace = Trace("f" * 32, logs_dir_for(tmp_path))
    with use_trace(trace), pytest.raises(LLMError):
        client(handler).complete([{"role": "user", "content": "hi"}])
    trace.finish(ok=False)

    call = events_by_name(trace.path)["llm.http"]
    assert call["status"] == 429
    assert call["ttfb_ms"] is not None
    assert "429" in call["error"]


def test_stream_timer_counts_stalls_and_reports_progress(monkeypatch) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr("backend.llm.client.time.perf_counter", lambda: clock["now"])
    timer = StreamTimer(checkpoint_seconds=1.0)
    timer.sent()
    clock["now"] += 0.2
    timer.headers(200)
    clock["now"] += 2.5
    timer.note("reasoning")
    clock["now"] += 0.1
    timer.note("text")
    clock["now"] += 1.5
    timer.note("text")

    fields = timer.fields()

    assert fields["ttfb_ms"] == 200.0
    assert fields["first_reasoning_ms"] == 2700.0
    assert fields["first_token_ms"] == 2800.0
    assert fields["gaps_ms"] == [2500.0, 1500.0]
    assert fields["stall_ms"] == 4000.0
    assert fields["longest_gap_ms"] == 2500.0
    assert fields["longest_gap_at_ms"] == 2700.0
    assert [point["t_ms"] for point in fields["checkpoints"]] == [2700.0, 4300.0]


def test_token_rates_use_the_measured_duration(monkeypatch) -> None:
    clock = {"now": 0.0}
    monkeypatch.setattr("backend.llm.client.time.perf_counter", lambda: clock["now"])
    timer = StreamTimer()
    timer.sent()
    clock["now"] += 2.0
    timer.note("text")
    reply = StreamedReply(
        text="x" * 100,
        prompt_tokens=10,
        completion_tokens=20,
        reasoning_tokens=5,
        finish_reason="stop",
        terminal=True,
    )

    fields = timer.token_fields(reply)

    assert fields["tokens_per_second"] == 10.0
    assert fields["text_chars_per_second"] == 50.0
    assert fields["terminal"] is True
