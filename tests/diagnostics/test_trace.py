import json
import time
from pathlib import Path

import pytest

from backend.diagnostics.trace import (
    Trace,
    current_trace,
    logs_dir_for,
    record,
    record_api_call,
    span,
    trace_for,
    use_trace,
)

TRACE_ID = "a" * 32


def read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_events_and_spans_are_written_with_offsets_and_a_summary(tmp_path: Path) -> None:
    trace = Trace(TRACE_ID, logs_dir_for(tmp_path))

    trace.event("queue.accepted", kind="create", guide_id="b" * 32)
    with trace.span("llm.http", model="deepseek/deepseek-v4.1-flash") as fields:
        fields["ttfb_ms"] = 120.5
        fields["first_token_ms"] = 900.0
    summary = trace.finish(ok=True, state="completed")

    events = read_events(trace.path)
    assert [event["event"] for event in events] == [
        "queue.accepted",
        "llm.http",
        "trace.summary",
    ]
    assert events[0]["t_ms"] >= 0
    assert events[0]["kind"] == "create"
    assert events[1]["ttfb_ms"] == 120.5
    assert events[1]["duration_ms"] >= 0
    assert events[2]["model_calls"] == 1
    assert events[2]["call_durations_ms"] == [events[1]["duration_ms"]]
    assert events[2]["call_ttfb_ms"] == [120.5]
    assert events[2]["ok"] is True
    assert summary["phases"]["llm.http"] == {
        "count": 1,
        "total_ms": events[1]["duration_ms"],
        "max_ms": events[1]["duration_ms"],
    }


def test_a_span_that_failed_is_still_measured_and_recorded(tmp_path: Path) -> None:
    trace = Trace(TRACE_ID, logs_dir_for(tmp_path))

    with pytest.raises(RuntimeError), trace.span("run.load_source"):
        raise RuntimeError("the source is gone")
    trace.finish(ok=False)

    events = read_events(trace.path)
    assert "RuntimeError: the source is gone" in events[0]["error"]
    assert events[0]["duration_ms"] >= 0
    assert events[-1]["ok"] is False


def test_the_summary_names_the_idle_time_between_model_calls(tmp_path: Path) -> None:
    trace = Trace(TRACE_ID, logs_dir_for(tmp_path))

    with trace.span("llm.http"):
        time.sleep(0.05)
    started = time.perf_counter()
    time.sleep(0.03)
    idle_ms = (time.perf_counter() - started) * 1000
    with trace.span("llm.http"):
        time.sleep(0.05)
    summary = trace.finish()

    assert summary["model_calls"] == 2
    gap = summary["idle_between_calls_ms"][0]
    # The slow call that precedes the pause is not itself a pause: measuring start
    # to start would report ~53ms here instead of the 30ms that was actually idle.
    assert idle_ms - 2 <= gap <= idle_ms + 20
    assert gap < 50


def test_a_reported_field_never_renames_its_own_record(tmp_path: Path) -> None:
    """A caller passing `name` must not turn a span into a phase of that name."""
    trace = Trace(TRACE_ID, logs_dir_for(tmp_path))

    trace.event("queue.accepted", name="Diagram")
    with trace.span("artifact.title", name="Diagram"):
        pass
    summary = trace.finish()

    events = read_events(trace.path)
    assert [event["event"] for event in events] == [
        "queue.accepted",
        "artifact.title",
        "trace.summary",
    ]
    assert "Diagram" not in summary["phases"]
    assert summary["phases"]["artifact.title"]["count"] == 1


def test_the_current_trace_is_thread_local_and_restored(tmp_path: Path) -> None:
    trace = trace_for(tmp_path, TRACE_ID)

    assert current_trace() is None
    with use_trace(trace):
        assert current_trace() is trace
        record("api.accepted", endpoint="POST /api/guides")
        with span("run.publish") as fields:
            fields["bytes"] = 12
    assert current_trace() is None

    events = read_events(trace.path)
    assert [event["event"] for event in events] == ["api.accepted", "run.publish"]
    assert events[1]["bytes"] == 12


def test_recording_without_a_trace_is_a_no_op() -> None:
    assert current_trace() is None
    record("nothing.happens", value=1)
    with span("nothing.either") as fields:
        fields["value"] = 1
    # No trace means no file, no error, and no state to leak into the next call.
    assert current_trace() is None


def test_disabled_tracing_writes_no_file(tmp_path: Path) -> None:
    trace = Trace(TRACE_ID, logs_dir_for(tmp_path), enabled=False)

    trace.event("ignored")
    with trace.span("ignored") as fields:
        fields["ignored"] = True
    assert trace.finish() == {}
    assert not trace.path.exists()


def test_one_request_reuses_the_same_trace(tmp_path: Path) -> None:
    first = trace_for(tmp_path, TRACE_ID)
    second = trace_for(tmp_path, TRACE_ID)

    assert first is second
    assert first.path.parent == logs_dir_for(tmp_path)


def test_the_stderr_mirror_names_the_step_and_its_fields(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("STUDY_FORGE_TRACE_MIRROR", "1")
    trace = Trace(TRACE_ID, logs_dir_for(tmp_path))

    trace.event("queue.accepted", kind="create")
    with trace.span("artifact.checker", ok=True):
        pass

    mirrored = capsys.readouterr().err
    assert "queue.accepted kind=create" in mirrored
    assert "artifact.checker" in mirrored


def test_the_api_log_records_requests_and_flags_polling(tmp_path: Path) -> None:
    record_api_call(
        logs_dir_for(tmp_path),
        method="POST",
        path="/api/guides",
        status=202,
        duration_ms=3.456,
        poll=False,
    )
    record_api_call(
        logs_dir_for(tmp_path),
        method="GET",
        path="/api/queue",
        status=200,
        duration_ms=0.5,
        poll=True,
    )

    events = read_events(logs_dir_for(tmp_path) / "api.jsonl")
    assert events[0]["path"] == "/api/guides"
    assert events[0]["duration_ms"] == 3.5
    assert events[1]["poll"] is True
