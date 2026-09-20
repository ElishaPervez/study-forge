"""Timing instrumentation for one accepted request.

Every request owns one trace: a JSONL file under ``<jobs_dir>/logs`` that
records, in arrival order, how long each step of the pipeline took - HTTP
acceptance, queue wait, source rasterization, every model call with its time to
first byte, time to first token, streamed gaps and token counts, math and font
work, the artifact checker subprocess, and publication. The last line of the
file is a summary, so one guide's whole story is readable without the report
script.

Deep code reaches the trace through a thread-local "current trace", so
instrumentation never has to be threaded through function signatures and a
missing trace is always a no-op. ``STUDY_FORGE_TRACE=0`` turns tracing off;
``STUDY_FORGE_TRACE_MIRROR=0`` keeps the files but stops echoing milestones to
stderr. ``python scripts/generation_report.py`` reads the files back as a
timeline.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

TRACE_ENV = "STUDY_FORGE_TRACE"
MIRROR_ENV = "STUDY_FORGE_TRACE_MIRROR"
LOGS_DIR_NAME = "logs"
TRACE_FILE_PREFIX = "generation-"
API_LOG_NAME = "api.jsonl"
MAX_REMEMBERED_TRACES = 512
_OFF = {"0", "false", "no", "off"}


def tracing_enabled() -> bool:
    return os.environ.get(TRACE_ENV, "1").strip().lower() not in _OFF


def mirror_enabled() -> bool:
    return os.environ.get(MIRROR_ENV, "1").strip().lower() not in _OFF


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def logs_dir_for(jobs_dir: Path) -> Path:
    return Path(jobs_dir) / LOGS_DIR_NAME


_WRITE_LOCK = threading.Lock()


def append_jsonl(path: Path, payload: Mapping[str, object]) -> None:
    """Append one event line. Instrumentation must never break the pipeline."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, default=str)
        with _WRITE_LOCK, path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
    except (OSError, TypeError, ValueError):
        return


def mirror_line(line: str) -> None:
    if not mirror_enabled():
        return
    try:
        print(line, file=sys.stderr, flush=True)
    except (OSError, ValueError):
        return


class Trace:
    """One request's timeline: events and measured spans, written as JSONL."""

    def __init__(self, trace_id: str, logs_dir: Path, *, enabled: bool = True) -> None:
        self.trace_id = trace_id
        self.logs_dir = Path(logs_dir)
        self.path = self.logs_dir / f"{TRACE_FILE_PREFIX}{trace_id}.jsonl"
        self.enabled = enabled
        self.started_at = now_iso()
        self._start = time.perf_counter()
        self._lock = threading.Lock()
        self._spans: list[dict[str, object]] = []
        self._finished = False

    # -- clock -------------------------------------------------------------

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._start) * 1000.0

    # -- recording ---------------------------------------------------------

    def event(self, step: str, *, mirror_event: bool = True, **fields: object) -> None:
        """Record one instant: what happened, and when relative to acceptance."""
        if not self.enabled:
            return
        offset = self.elapsed_ms()
        reported = {key: value for key, value in fields.items() if value is not None}
        # Reported fields come first so a field a caller happens to call "event"
        # or "at" can never rename the record it belongs to.
        append_jsonl(
            self.path,
            {
                "at": now_iso(),
                **reported,
                "t_ms": round(offset, 1),
                "event": step,
                "thread": threading.current_thread().name,
            },
        )
        if mirror_event:
            mirror_line(
                f"[trace {self.trace_id[:8]} +{offset / 1000:.3f}s] {step}"
                f"{_format_fields(reported)}"
            )

    @contextmanager
    def span(self, step: str, **fields: object) -> Iterator[dict[str, object]]:
        """Measure one step. The yielded dict adds fields reported on exit."""
        if not self.enabled:
            yield {}
            return
        started = time.perf_counter()
        started_offset = self.elapsed_ms()
        extra: dict[str, object] = {}
        try:
            yield extra
        except BaseException as error:
            extra["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1000.0
            self._record_span(step, started_offset, duration_ms, {**fields, **extra})

    def _record_span(
        self, step: str, started_offset: float, duration_ms: float, fields: dict[str, object]
    ) -> None:
        reported = {key: value for key, value in fields.items() if value is not None}
        rounded = round(duration_ms, 1)
        record: dict[str, object] = {
            "t_ms": round(started_offset, 1),
            "duration_ms": rounded,
            **reported,
            "name": step,
        }
        with self._lock:
            self._spans.append(record)
        append_jsonl(
            self.path,
            {
                "at": now_iso(),
                **reported,
                "t_ms": round(started_offset, 1),
                "event": step,
                "duration_ms": rounded,
                "thread": threading.current_thread().name,
            },
        )
        mirror_line(
            f"[trace {self.trace_id[:8]} {started_offset / 1000:.3f}s→"
            f"{(started_offset + duration_ms) / 1000:.3f}s] {step} "
            f"{rounded / 1000:.3f}s{_format_fields(reported)}"
        )

    # -- summary -----------------------------------------------------------

    def summary(self, **fields: object) -> dict[str, object]:
        """Aggregate the spans: where the time went and what the model cost."""
        phases: dict[str, dict[str, object]] = {}
        for record in self._spans:
            bucket = phases.setdefault(
                str(record["name"]), {"count": 0, "total_ms": 0.0, "max_ms": 0.0}
            )
            bucket["count"] = int(bucket["count"]) + 1
            bucket["total_ms"] = round(float(bucket["total_ms"]) + float(record["duration_ms"]), 1)
            bucket["max_ms"] = round(max(float(bucket["max_ms"]), float(record["duration_ms"])), 1)
        calls = [record for record in self._spans if record["name"] == "llm.http"]
        calls = sorted(calls, key=lambda record: float(record["t_ms"]))
        attempts = sum(1 for record in self._spans if record["name"] == "llm.attempt")
        # Idle time between calls: the next call's start minus the previous call's
        # end. Subtracting start offsets would report call duration as if it were
        # a gap, which makes an instant tool read look like a multi-second pause.
        between = [
            round(
                max(
                    float(later["t_ms"])
                    - (float(earlier["t_ms"]) + float(earlier["duration_ms"])),
                    0.0,
                ),
                1,
            )
            for earlier, later in pairwise(calls)
        ]
        return {
            "trace_id": self.trace_id,
            "started_at": self.started_at,
            "total_ms": round(self.elapsed_ms(), 1),
            "model_calls": len(calls),
            "attempts": attempts,
            "call_offsets_ms": [float(record["t_ms"]) for record in calls],
            "call_durations_ms": [float(record["duration_ms"]) for record in calls],
            "call_ttfb_ms": [record.get("ttfb_ms") for record in calls],
            "call_first_token_ms": [record.get("first_token_ms") for record in calls],
            "call_reasoning_tokens": [record.get("reasoning_tokens") or 0 for record in calls],
            # Carried on the summary so two runs of the same unit stay
            # distinguishable: a trace that does not name its effort cannot be
            # compared against one that does.
            "reasoning_effort": next(
                (record["reasoning_effort"] for record in calls if record.get("reasoning_effort")),
                None,
            ),
            "idle_between_calls_ms": between,
            "phases": phases,
            **{key: value for key, value in fields.items() if value is not None},
        }

    def finish(self, **fields: object) -> dict[str, object]:
        """Write the summary line once, and return it."""
        if not self.enabled or self._finished:
            return {}
        self._finished = True
        summary = self.summary(**fields)
        append_jsonl(
            self.path,
            {"at": now_iso(), "t_ms": round(self.elapsed_ms(), 1), "event": "trace.summary", **summary},
        )
        mirror_line(
            f"[trace {self.trace_id[:8]} +{float(summary['total_ms']) / 1000:.3f}s] SUMMARY "
            f"total={float(summary['total_ms']) / 1000:.3f}s "
            f"http_calls={summary['model_calls']} attempts={summary['attempts']} "
            f"calls={_format_seconds(summary['call_durations_ms'])}"
        )
        return summary


def _format_fields(fields: Mapping[str, object]) -> str:
    if not fields:
        return ""
    return " " + " ".join(f"{key}={_short(value)}" for key, value in fields.items())


def _format_seconds(values: object) -> str:
    if not isinstance(values, list):
        return "[]"
    return "[" + ", ".join(f"{float(value) / 1000:.2f}s" for value in values) + "]"


def _short(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.1f}"
    text = str(value)
    return text if len(text) <= 80 else text[:77] + "..."


# -- registry and thread-local access --------------------------------------

_REGISTRY: dict[str, Trace] = {}
_REGISTRY_LOCK = threading.Lock()
_LOCAL = threading.local()


def trace_for(jobs_dir: Path, trace_id: str) -> Trace:
    """The trace for one request, created on first use and reused afterwards."""
    logs_dir = logs_dir_for(jobs_dir)
    with _REGISTRY_LOCK:
        existing = _REGISTRY.get(f"{logs_dir}|{trace_id}")
        if existing is not None:
            return existing
        trace = Trace(trace_id, logs_dir, enabled=tracing_enabled())
        if len(_REGISTRY) >= MAX_REMEMBERED_TRACES:
            _REGISTRY.clear()
        _REGISTRY[f"{logs_dir}|{trace_id}"] = trace
        return trace


def current_trace() -> Trace | None:
    return getattr(_LOCAL, "trace", None)


@contextmanager
def use_trace(trace: Trace | None) -> Iterator[Trace | None]:
    """Make one trace current for this thread while a step runs."""
    previous = current_trace()
    _LOCAL.trace = trace
    try:
        yield trace
    finally:
        _LOCAL.trace = previous


def record(step: str, *, mirror_event: bool = True, **fields: object) -> None:
    """Record an instant on the current trace, or do nothing outside one."""
    trace = current_trace()
    if trace is not None:
        trace.event(step, mirror_event=mirror_event, **fields)


@contextmanager
def span(step: str, **fields: object) -> Iterator[dict[str, object]]:
    """Measure a step on the current trace, or do nothing outside one."""
    trace = current_trace()
    if trace is None:
        yield {}
        return
    with trace.span(step, **fields) as extra:
        yield extra


def elapsed_ms() -> float:
    trace = current_trace()
    return trace.elapsed_ms() if trace is not None else 0.0


def record_api_call(
    logs_dir: Path,
    *,
    method: str,
    path: str,
    status: int,
    duration_ms: float,
    poll: bool = False,
) -> None:
    """Append one HTTP request to the shared api log, for click-to-result timing."""
    if not tracing_enabled():
        return
    append_jsonl(
        Path(logs_dir) / API_LOG_NAME,
        {
            "at": now_iso(),
            "method": method,
            "path": path,
            "status": status,
            "duration_ms": round(duration_ms, 1),
            "poll": poll,
        },
    )
