"""Read generation traces back as a timeline.

Every accepted request writes ``<jobs_dir>/logs/generation-<receipt>.jsonl``
(see ``backend/diagnostics/trace.py``). This script is the reader: where the
time went, how many model calls there were, how long each call took to be
acknowledged and to produce its first token, how long the gaps between calls
were, and whether the time inside a call was generation or provider stall.

    uv run python scripts/generation_report.py                 # latest trace, in full
    uv run python scripts/generation_report.py --all           # one line per trace
    uv run python scripts/generation_report.py --trace 4f2a    # match a receipt
    uv run python scripts/generation_report.py --jobs-dir jobs
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import pairwise
from pathlib import Path

DEFAULT_JOBS_DIR = Path("jobs")
LOGS_DIR_NAME = "logs"
TRACE_GLOB = "generation-*.jsonl"
MILESTONES = (
    "api.accepted",
    "queue.accepted",
    "queue.run.started",
    "run.started",
    "run.load_source",
    "source.ready",
    "source.inputs",
    "payload.images",
    "llm.call.started",
    "llm.http",
    "llm.http.done",
    "llm.call.finished",
    "tool.read_reference",
    "artifact.build",
    "artifact.mathml",
    "artifact.fonts",
    "artifact.write",
    "artifact.policy",
    "artifact.checker",
    "checker.subprocess",
    "run.publish",
    "queue.settled",
    "run.failed",
    "trace.summary",
)
# Phases worth calling out in the verdict, in the order a guide passes through them.
VERDICT_PHASES = (
    "queue.run.started",
    "run.load_source",
    "source.inputs",
    "payload.images",
    "llm.http",
    "artifact.build",
    "artifact.mathml",
    "artifact.fonts",
    "artifact.write",
    "artifact.policy",
    "artifact.checker",
    "checker.subprocess",
    "run.publish",
)


def load_events(path: Path) -> list[dict]:
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def trace_paths(logs_dir: Path, jobs_dir: Path) -> list[Path]:
    for candidate in (logs_dir, jobs_dir / LOGS_DIR_NAME):
        if candidate.is_dir():
            paths = sorted(
                candidate.glob(TRACE_GLOB), key=lambda path: path.stat().st_mtime, reverse=True
            )
            if paths:
                return paths
    return []


def select_traces(paths: list[Path], needle: str | None, show_all: bool) -> list[Path]:
    if needle is not None:
        matches = [path for path in paths if needle in path.name]
        if not matches:
            raise SystemExit(f"no trace matches {needle!r}; found {len(paths)} trace(s)")
        return matches[:1]
    return paths if show_all else paths[:1]


def seconds(value: object) -> str:
    if value is None:
        return "—"
    return f"{float(value) / 1000:.2f}s"


def think_ms(call: dict) -> float | None:
    """Hidden reasoning before the first visible token: first token minus first event."""
    first = call.get("first_token_ms")
    any_ = call.get("first_any_ms")
    if first is None or any_ is None:
        return None
    return max(float(first) - float(any_), 0.0)


def signed_seconds(value: float) -> str:
    """A step's offset, including the small negatives a nested span can produce."""
    return f"{value / 1000:+.3f}s"


def summary_of(events: list[dict]) -> dict:
    summaries = [event for event in events if event.get("event") == "trace.summary"]
    return summaries[-1] if summaries else {}


def phase_totals(events: list[dict]) -> dict[str, dict[str, float]]:
    totals: dict[str, dict[str, float]] = {}
    for event in events:
        if "duration_ms" not in event:
            continue
        name = str(event.get("event"))
        if name == "trace.summary":
            continue
        bucket = totals.setdefault(name, {"count": 0.0, "total_ms": 0.0, "max_ms": 0.0})
        duration = float(event["duration_ms"])
        bucket["count"] += 1
        bucket["total_ms"] += duration
        bucket["max_ms"] = max(bucket["max_ms"], duration)
    return totals


def model_calls(events: list[dict]) -> list[dict]:
    calls = [event for event in events if event.get("event") == "llm.http"]
    return sorted(calls, key=lambda event: float(event.get("t_ms", 0.0)))


def format_timeline(events: list[dict]) -> list[str]:
    lines: list[str] = []
    previous: float | None = None
    for event in events:
        name = str(event.get("event"))
        if name not in MILESTONES:
            continue
        offset = float(event.get("t_ms", 0.0))
        delta = "" if previous is None else f" ({signed_seconds(offset - previous)})"
        previous = offset
        detail = []
        for key, value in event.items():
            if key in {"at", "t_ms", "event", "thread", "duration_ms"} or value is None:
                continue
            detail.append(f"{key}={value}")
        suffix = f" [{' '.join(detail)}]" if detail else ""
        duration = ""
        if event.get("duration_ms") is not None:
            duration = f" {seconds(event['duration_ms'])}"
        lines.append(f"  {offset / 1000:8.3f}s{delta:>13}  {name}{duration}{suffix}")
    return lines


def format_calls(calls: list[dict]) -> list[str]:
    lines: list[str] = []
    lines.append(
        "  #   started   duration     ttfb  1st token  1st(any)    think  stream   tokens"
        "   r-tok   tok/s  finish"
    )
    for index, call in enumerate(calls, start=1):
        stream_ms = call.get("stream_ms")
        lines.append(
            "  {index:<3} {started:>7} {duration:>10} {ttfb:>8} {first:>10} {any_:>9} "
            "{think:>8} {stream:>7} {tokens:>8} {rtok:>7} {rate:>7}  {finish}".format(
                index=index,
                started=f"{float(call.get('t_ms', 0.0)) / 1000:.2f}s",
                duration=seconds(call.get("duration_ms")),
                ttfb=seconds(call.get("ttfb_ms")),
                first=seconds(call.get("first_token_ms")),
                any_=seconds(call.get("first_any_ms")),
                think=seconds(think_ms(call)),
                stream=seconds(stream_ms),
                tokens=str(call.get("completion_tokens") or "—"),
                rtok=str(call.get("reasoning_tokens") or "—"),
                rate=str(call.get("tokens_per_second") or "—"),
                finish=str(call.get("finish_reason") or "—"),
            )
        )
    return lines


def format_thinking(calls: list[dict]) -> list[str]:
    """How much of the model time was hidden reasoning before visible output."""
    if not calls:
        return []
    reasoning = sum(float(call.get("reasoning_tokens") or 0.0) for call in calls)
    completion = sum(float(call.get("completion_tokens") or 0.0) for call in calls)
    thinks = [value for call in calls if (value := think_ms(call)) is not None]
    lines = [
        f"  reasoning tokens: {int(reasoning)} of {int(completion)} completion "
        f"({reasoning / completion * 100:.1f}%)" if completion else "  reasoning tokens: none",
    ]
    if thinks:
        lines.append(
            f"  silent thinking before the first visible token: "
            f"{seconds(sum(thinks))} across {len(thinks)} call(s), "
            f"worst {seconds(max(thinks))}"
        )
    return lines


def format_gaps(calls: list[dict]) -> list[str]:
    if len(calls) < 2:
        return []
    lines = ["  between calls (previous call finished → next call sent):"]
    for index, (previous, following) in enumerate(pairwise(calls), start=1):
        end = float(previous.get("t_ms", 0.0)) + float(previous.get("duration_ms", 0.0))
        start = float(following.get("t_ms", 0.0))
        lines.append(
            f"    call {index} → {index + 1}: {seconds(start - end)} "
            f"(from {end / 1000:.2f}s to {start / 1000:.2f}s)"
        )
    return lines


def format_stalls(calls: list[dict]) -> list[str]:
    lines: list[str] = []
    for index, call in enumerate(calls, start=1):
        gaps = call.get("gaps_ms")
        checkpoints = call.get("checkpoints")
        if gaps:
            lines.append(
                f"  call {index}: {len(gaps)} stall(s) over 1s, {seconds(call.get('stall_ms'))} "
                f"stalled, longest {seconds(call.get('longest_gap_ms'))} at "
                f"{seconds(call.get('longest_gap_at_ms'))}: "
                + ", ".join(seconds(gap) for gap in gaps)
            )
        if checkpoints:
            progress = ", ".join(
                f"{seconds(point.get('t_ms'))}: text={point.get('text_chars')} "
                f"reasoning={point.get('reasoning_chars')}"
                for point in checkpoints
                if isinstance(point, dict)
            )
            lines.append(f"  call {index} progress: {progress}")
    return lines


def print_trace(path: Path) -> None:
    events = load_events(path)
    summary = summary_of(events)
    totals = phase_totals(events)
    calls = model_calls(events)
    print(f"trace:      {path.name}")
    print(f"file:       {path}")
    print(f"started:    {summary.get('started_at') or (events[0].get('at') if events else 'unknown')}")
    print(
        "outcome:    "
        + ", ".join(
            f"{key}={summary.get(key)}"
            for key in ("state", "ok", "kind", "guide_id", "queued_ms", "error")
            if summary.get(key) is not None
        )
    )
    print(f"total:      {seconds(summary.get('total_ms'))} from acceptance")
    print(f"effort:     {summary.get('reasoning_effort') or 'not recorded'}")
    print(
        f"model calls: {len(calls)} http call(s), "
        f"{summary.get('attempts', 0)} writing attempt(s)"
    )
    if calls:
        first = float(calls[0].get("t_ms", 0.0))
        print(
            f"first request: {seconds(first)} after acceptance "
            "(each call's own numbers are in the table below)"
        )
    print()
    print("where the time went (total / count / worst):")
    if not totals:
        print("  no measured spans in this trace")
    accounted = 0.0
    total_ms = float(summary.get("total_ms", 0.0)) or None
    for name, bucket in sorted(totals.items(), key=lambda item: item[1]["total_ms"], reverse=True):
        share = ""
        if total_ms:
            share = f"  {bucket['total_ms'] / total_ms * 100:5.1f}%"
        print(
            f"  {name:<24} {seconds(bucket['total_ms']):>9}{share}  "
            f"x{int(bucket['count']):<3} worst {seconds(bucket['max_ms'])}"
        )
        accounted += bucket["total_ms"]
    if total_ms:
        # Overlapping spans (a phase and the sub-steps inside it) both count here,
        # so the sum can exceed the total; the per-phase rows are what to compare.
        print(f"  {'(sum of spans)':<24} {seconds(accounted):>9}")
    print()
    print("timeline:")
    for line in format_timeline(events):
        print(line)
    if calls:
        print()
        print("model calls:")
        for line in format_calls(calls):
            print(line)
        for line in format_thinking(calls):
            print(line)
        for line in format_gaps(calls):
            print(line)
        for line in format_stalls(calls):
            print(line)
    verdict = [
        (name, totals[name]["total_ms"])
        for name in VERDICT_PHASES
        if name in totals
    ]
    if verdict:
        print()
        print("verdict:")
        for name, duration in sorted(verdict, key=lambda item: item[1], reverse=True)[:6]:
            print(f"  {name:<24} {seconds(duration):>9}")
    print()


def print_overview(paths: list[Path]) -> None:
    print(
        "receipt   total   calls  ttfb(1st)  token(1st)  effort   model  checker  queue  state"
    )
    for path in paths:
        events = load_events(path)
        summary = summary_of(events)
        calls = model_calls(events)
        totals = phase_totals(events)
        model_ms = sum(float(call.get("duration_ms", 0.0)) for call in calls)
        checker_ms = (
            totals.get("artifact.checker", {}).get("total_ms", 0.0)
            + totals.get("checker.subprocess", {}).get("total_ms", 0.0)
        )
        print(
            "{receipt:<9} {total:>6} {calls:>6} {ttfb:>10} {first:>11} {effort:>7} {model:>6} "
            "{checker:>8} {queue:>6}  {state}".format(
                receipt=path.stem.removeprefix("generation-")[:8],
                total=seconds(summary.get("total_ms")),
                calls=len(calls),
                ttfb=seconds(calls[0].get("ttfb_ms")) if calls else "—",
                first=seconds(calls[0].get("first_token_ms")) if calls else "—",
                effort=summary.get("reasoning_effort") or "—",
                model=seconds(model_ms),
                checker=seconds(checker_ms),
                queue=seconds(summary.get("queued_ms")),
                state=summary.get("state") or "unfinished",
            )
        )


def main(argv: list[str] | None = None) -> int:
    # Traces carry arrows and dashes; a cp1252 console refuses to print them.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        pass
    parser = argparse.ArgumentParser(description="Read generation traces as a timeline.")
    parser.add_argument("--jobs-dir", type=Path, default=DEFAULT_JOBS_DIR)
    parser.add_argument("--trace", help="receipt (or any part of the file name) to show")
    parser.add_argument("--all", action="store_true", help="one line per trace")
    parser.add_argument("--limit", type=int, default=20, help="rows in --all mode")
    args = parser.parse_args(argv)

    logs_dir = args.jobs_dir / LOGS_DIR_NAME
    paths = trace_paths(logs_dir, args.jobs_dir)
    if not paths:
        print(f"no traces under {logs_dir} yet")
        return 1
    if args.all:
        print_overview(paths[: max(args.limit, 1)])
        return 0
    for path in select_traces(paths, args.trace, show_all=False):
        print_trace(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
