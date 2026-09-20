import json

import pytest

from backend.llm.stream import StreamAccumulator, StreamError


def line(payload: object) -> str:
    return f"data: {json.dumps(payload)}"


def feed(accumulator: StreamAccumulator, *payloads: object) -> None:
    for payload in payloads:
        accumulator.feed_line(line(payload))


def delta(content: str, **extra: object) -> dict:
    return {"choices": [{"delta": {"content": content}, **extra}]}


def test_text_is_counted_once_when_it_arrives_in_pieces() -> None:
    pieces: list[str] = []
    accumulator = StreamAccumulator(on_text=pieces.append)

    feed(
        accumulator,
        delta("<html><body>"),
        delta("Cell division"),
        delta("</body></html>", finish_reason="stop"),
    )
    result = accumulator.result()

    assert result.text == "<html><body>Cell division</body></html>"
    assert pieces == ["<html><body>", "Cell division", "</body></html>"]
    assert result.finish_reason == "stop"
    assert result.terminal is True


def test_keepalive_comments_and_blank_lines_are_not_output() -> None:
    pieces: list[str] = []
    accumulator = StreamAccumulator(on_text=pieces.append)

    accumulator.feed_line(": OPENROUTER PROCESSING")
    accumulator.feed_line("")
    accumulator.feed_line("event: message")
    feed(accumulator, delta("real text"))
    accumulator.feed_line("data: [DONE]")

    assert accumulator.result().text == "real text"
    assert pieces == ["real text"]
    assert accumulator.done is True
    assert accumulator.result().terminal is True


def test_usage_messages_without_choices_keep_the_reply_alive() -> None:
    accumulator = StreamAccumulator()

    feed(accumulator, delta("<html></html>", finish_reason="stop"))
    feed(
        accumulator,
        {
            "choices": [],
            "usage": {
                "prompt_tokens": 13833,
                "completion_tokens": 32768,
                "completion_tokens_details": {"reasoning_tokens": 32768},
            },
        },
    )
    accumulator.feed_line("data: [DONE]")

    result = accumulator.result()
    assert result.text == "<html></html>"
    assert result.prompt_tokens == 13833
    assert result.completion_tokens == 32768
    assert result.reasoning_tokens == 32768
    assert result.terminal is True


def test_repeated_finish_information_is_idempotent() -> None:
    accumulator = StreamAccumulator()

    feed(accumulator, delta("text", finish_reason="stop"))
    feed(accumulator, delta("", finish_reason="stop"))
    accumulator.feed_line("data: [DONE]")
    accumulator.feed_line("data: [DONE]")

    assert accumulator.result().text == "text"
    assert accumulator.result().finish_reason == "stop"


def test_fragmented_tool_names_and_arguments_are_joined() -> None:
    accumulator = StreamAccumulator()

    feed(
        accumulator,
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
                            {"index": 0, "function": {"name": "erence", "arguments": 'process.md"}'}}
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
    )

    fragments = accumulator.result().tool_calls
    assert len(fragments) == 1
    assert fragments[0].call_id == "call_1"
    assert fragments[0].name == "read_reference"
    assert fragments[0].arguments == '{"name":"type-process.md"}'
    assert accumulator.result().finish_reason == "tool_calls"


def test_multiple_indexed_tool_calls_keep_their_order() -> None:
    accumulator = StreamAccumulator()

    feed(
        accumulator,
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 1, "id": "call_b", "function": {"name": "list_references"}},
                            {"index": 0, "id": "call_a", "function": {"name": "read_reference"}},
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
                            {"index": 0, "function": {"arguments": "{}"}},
                            {"index": 1, "function": {"arguments": "{}"}},
                        ]
                    }
                }
            ]
        },
    )

    fragments = accumulator.result().tool_calls
    assert [fragment.index for fragment in fragments] == [0, 1]
    assert [fragment.call_id for fragment in fragments] == ["call_a", "call_b"]
    assert [fragment.arguments for fragment in fragments] == ["{}", "{}"]


def test_hidden_reasoning_is_kept_out_of_the_reply_text() -> None:
    accumulator = StreamAccumulator()

    feed(accumulator, {"choices": [{"delta": {"reasoning": "thinking"}}]})
    feed(accumulator, delta("<html></html>", finish_reason="stop"))

    result = accumulator.result()
    assert result.text == "<html></html>"
    assert result.reasoning == "thinking"


def test_a_completion_without_text_or_tools_still_reports_its_reason() -> None:
    accumulator = StreamAccumulator()

    feed(accumulator, {"choices": [{"delta": {"content": None}, "finish_reason": "length"}]})

    result = accumulator.result()
    assert result.text == ""
    assert result.finish_reason == "length"
    assert result.terminal is True


@pytest.mark.parametrize("bad_line", ["data: {not json", 'data: ["a list"]', "data: 4"])
def test_malformed_event_data_is_reported(bad_line: str) -> None:
    accumulator = StreamAccumulator()

    with pytest.raises(StreamError):
        accumulator.feed_line(bad_line)


def test_an_error_after_output_began_is_remembered() -> None:
    accumulator = StreamAccumulator()

    feed(accumulator, delta("part of a guide"))
    feed(accumulator, {"error": {"message": "provider timeout"}})

    assert accumulator.error == "provider timeout"
    assert accumulator.result().text == "part of a guide"
    assert accumulator.terminal is False


def test_a_missing_terminal_indication_is_not_a_complete_reply() -> None:
    accumulator = StreamAccumulator()

    feed(accumulator, delta("<html><body>cut off"))
    accumulator.feed_line("data:")

    assert accumulator.result().text == "<html><body>cut off"
    assert accumulator.result().terminal is False
