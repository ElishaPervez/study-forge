from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from backend.diagnostics.trace import record, span
from backend.fonts.embed import inject_fonts
from backend.generate.progress import (
    ACTIVITY_CHECKING,
    ACTIVITY_CORRECTING,
    ACTIVITY_PREPARING,
    ACTIVITY_REFERENCES,
    ACTIVITY_RETRYING,
    ACTIVITY_WAITING,
    ProgressReporter,
)
from backend.generate.unit import (
    MAX_CALLS,
    MAX_TOOL_TURNS,
    STOPPED_FINDING,
    _backoff_seconds,
    _empty_reply_finding,
    _empty_reply_prompt,
    _repair_prompt,
    _resolve_tool_calls,
    _strip_fences,
    _tools,
    _truncated_reply_finding,
    _v1_output_policy_findings,
    stopped_work,
)
from backend.ingest.source import source_inputs
from backend.jobs.schema import Selection, SourceAsset
from backend.llm.client import LLM, LLMError, LLMReply, LLMStopped, image_part
from backend.mathml.render import render_math
from backend.prompt.build import build_revision_message
from backend.skill.bundle import Bundle
from backend.verify.self_check import CheckResult, run_self_check


@dataclass(frozen=True)
class RevisionRequest:
    guide_id: str
    source: SourceAsset
    selection: Selection
    selected_text: str
    instruction: str
    mode: str
    current_html: str


@dataclass(frozen=True)
class RevisionResult:
    html: str | None
    calls: int
    requested_refs: list[str]
    findings: list[str]


def revise_guide(
    request: RevisionRequest,
    *,
    source_root: Path | None = None,
    llm: LLM,
    bundle: Bundle,
    fonts_css: str,
    max_calls: int = MAX_CALLS,
    checker: Callable[[Path, Path], CheckResult] = run_self_check,
    progress: ProgressReporter | None = None,
    stop: Event | None = None,
) -> RevisionResult:
    if progress is not None:
        progress.set_activity(ACTIVITY_PREPARING)
    with span(
        "source.inputs",
        kind=request.source.kind,
        mode=request.selection.get("mode"),
        files=len(request.source.files),
    ) as source_span:
        inputs = source_inputs(request.source, request.selection, source_root)
        source_span["images"] = len(inputs)
    record("source.ready", images=len(inputs))
    if stopped_work(stop):
        return RevisionResult(None, 0, [], [STOPPED_FINDING])
    content: list[dict] = []
    with span("payload.images", count=len(inputs)) as encoding:
        for image in inputs:
            content.append(image_part(image))
        encoding["payload_chars"] = sum(len(part["image_url"]["url"]) for part in content)
    content.append(
        {
            "type": "text",
            "text": (
                f"Revision mode: {request.mode}.\n\n"
                + build_revision_message(
                    request.selected_text,
                    request.instruction,
                    request.current_html,
                )
            ),
        }
    )
    messages: list[dict] = [
        {"role": "system", "content": bundle.system_prompt},
        {"role": "user", "content": content},
    ]
    budget = max(0, min(MAX_CALLS, max_calls))
    calls = 0
    requested: list[str] = []
    last_findings: list[str] = []
    repair_used = False
    attempts = 0
    tool_turns = 0

    while attempts < budget:
        if stopped_work(stop):
            return RevisionResult(None, calls, list(requested), [*last_findings, STOPPED_FINDING])
        if tool_turns >= MAX_TOOL_TURNS:
            last_findings = [
                "reference lookups exhausted their allowance before a revision was written"
            ]
            break
        calls += 1
        if progress is not None:
            progress.begin_attempt()
            progress.set_activity(ACTIVITY_WAITING)
        attempt = attempts + 1
        record(
            "llm.call.started",
            call=calls,
            attempt=attempt,
            tool_turn=tool_turns,
            messages=len(messages),
            repair=repair_used,
            revision=request.mode,
        )
        try:
            with span(
                "llm.attempt",
                call=calls,
                attempt=attempt,
                messages=len(messages),
                repair=repair_used,
            ) as attempt_span:
                reply: LLMReply = llm.complete(
                    messages,
                    tools=_tools(bundle),
                    on_text=progress.observe if progress is not None else None,
                    stop=stop,
                )
                attempt_span["text_chars"] = len(reply.text or "")
                attempt_span["tool_calls"] = len(reply.tool_calls) or None
                attempt_span["finish_reason"] = reply.finish_reason or None
        except LLMStopped:
            return RevisionResult(None, calls, list(requested), [*last_findings, STOPPED_FINDING])
        except LLMError as error:
            attempts += 1
            if not error.retryable or attempts >= budget:
                return RevisionResult(None, calls, list(requested), [*last_findings, str(error)])
            if progress is not None:
                progress.set_activity(ACTIVITY_RETRYING)
            backoff = _backoff_seconds(calls)
            record("llm.retry_backoff", seconds=backoff, error=str(error))
            time.sleep(backoff)
            continue

        if reply.tool_calls:
            tool_turns += 1
            if progress is not None:
                progress.discard_attempt_progress()
                progress.set_activity(ACTIVITY_REFERENCES)
            messages.append(
                {
                    "role": "assistant",
                    "content": reply.text or "",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": call.arguments},
                        }
                        for call in reply.tool_calls
                    ],
                }
            )
            messages.extend(_resolve_tool_calls(reply.tool_calls, bundle, requested))
            continue

        attempts += 1
        if not (reply.text or "").strip():
            # An empty reply is not a bad revision, it is no revision at all: saying
            # so beats reporting the output policy against a document never written.
            last_findings = [_empty_reply_finding(reply)]
            if repair_used or attempts >= budget:
                return RevisionResult(None, calls, list(requested), last_findings)
            repair_used = True
            messages.append({"role": "user", "content": _empty_reply_prompt(last_findings[0])})
            if progress is not None:
                progress.set_activity(ACTIVITY_CORRECTING)
            record("repair.empty_reply", call=calls, finding=last_findings[0])
            continue

        if stopped_work(stop):
            return RevisionResult(None, calls, list(requested), [*last_findings, STOPPED_FINDING])
        with span("artifact.build", reply_chars=len(reply.text or "")) as build:
            with span("artifact.strip_fences"):
                document = _strip_fences(reply.text or "")
            with span("artifact.mathml", chars=len(document)) as mathml:
                rendered = render_math(document)
                mathml["html_chars"] = len(rendered)
                mathml["math_elements"] = rendered.count("<math")
            with span("artifact.fonts", chars=len(rendered)):
                candidate = inject_fonts(rendered, fonts_css)
            build["html_chars"] = len(candidate)
        if progress is not None:
            progress.set_activity(ACTIVITY_CHECKING)
        with span("artifact.verify", chars=len(candidate)) as verifying:
            findings = _validated_findings(candidate, bundle.skill_dir, checker)
            verifying["findings"] = len(findings) or None
        if not findings:
            return RevisionResult(candidate, calls, list(requested), [])

        if reply.finish_reason == "length":
            findings.append(_truncated_reply_finding(reply))
        last_findings = findings
        if repair_used or attempts >= budget:
            return RevisionResult(None, calls, list(requested), last_findings)

        repair_used = True
        messages.append({"role": "assistant", "content": reply.text or ""})
        messages.append({"role": "user", "content": _repair_prompt(last_findings)})
        if progress is not None:
            progress.set_activity(ACTIVITY_CORRECTING)
        record("repair.requested", call=calls, findings=len(last_findings))

    return RevisionResult(
        None,
        calls,
        list(requested),
        last_findings or ["call budget exhausted before a valid revision was returned"],
    )


def _validated_findings(
    html: str, skill_dir: Path, checker: Callable[[Path, Path], CheckResult]
) -> list[str]:
    with span("artifact.policy", chars=len(html)) as policy:
        policy_findings = _v1_output_policy_findings(html)
        policy["findings"] = len(policy_findings) or None
    candidate_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            suffix=".html",
            delete=False,
        ) as candidate_file:
            candidate_path = Path(candidate_file.name)
            candidate_file.write(html)
            candidate_file.flush()
        with span("artifact.checker") as checking:
            result = checker(candidate_path, skill_dir)
            checking["ok"] = result.ok
            checking["findings"] = len(result.findings) or None
    finally:
        if candidate_path is not None:
            candidate_path.unlink(missing_ok=True)
    if policy_findings:
        return [*policy_findings, *result.findings]
    if not result.ok:
        return result.findings or ["artifact checker rejected the candidate without findings"]
    return []
