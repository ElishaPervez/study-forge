from pathlib import Path

from backend.diagnostics.trace import Trace, logs_dir_for
from scripts.generation_report import main


def write_trace(tmp_path: Path, trace_id: str, calls: int) -> Trace:
    trace = Trace(trace_id, logs_dir_for(tmp_path))
    trace.event("api.accepted", elapsed_ms=1.0, kind="create")
    trace.event("queue.accepted", kind="create")
    trace.event("queue.run.started", queued_ms=5.0)
    for index in range(calls):
        with trace.span(
            "llm.http",
            ttfb_ms=100.0 + index,
            first_token_ms=200.0 + index,
            completion_tokens=10,
            reasoning_effort="low",
            finish_reason="stop",
        ):
            pass
    with trace.span("artifact.checker", ok=True):
        pass
    trace.finish(ok=True, state="completed")
    return trace


def test_the_report_prints_a_timeline_and_a_call_table(tmp_path: Path, capsys) -> None:
    write_trace(tmp_path, "1" * 32, 2)

    assert main(["--jobs-dir", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert "model calls: 2" in out
    assert "effort:     low" in out
    assert "timeline:" in out
    assert "llm.http" in out
    assert "between calls (previous call finished → next call sent):" in out
    assert "artifact.checker" in out
    assert "verdict:" in out


def test_all_mode_lists_every_trace_and_can_select_one(tmp_path: Path, capsys) -> None:
    write_trace(tmp_path, "1" * 32, 1)
    write_trace(tmp_path, "2" * 32, 3)

    assert main(["--jobs-dir", str(tmp_path), "--all"]) == 0
    overview = capsys.readouterr().out
    assert "11111111" in overview
    assert "22222222" in overview
    # The overview is how two runs of the same unit get compared, so the effort
    # each one used has to be readable without opening the JSONL.
    assert "low" in overview

    assert main(["--jobs-dir", str(tmp_path), "--trace", "22222222"]) == 0
    selected = capsys.readouterr().out
    assert "22222222" in selected
    assert "11111111" not in selected


def test_the_report_says_so_when_nothing_has_run_yet(tmp_path: Path, capsys) -> None:
    assert main(["--jobs-dir", str(tmp_path)]) == 1
    assert "no traces" in capsys.readouterr().out


def test_an_unknown_trace_is_reported_instead_of_crashing(tmp_path: Path) -> None:
    write_trace(tmp_path, "1" * 32, 1)

    try:
        main(["--jobs-dir", str(tmp_path), "--trace", "does-not-exist"])
    except SystemExit as error:
        assert "no trace matches" in str(error)
    else:
        raise AssertionError("an unknown trace must be refused")
