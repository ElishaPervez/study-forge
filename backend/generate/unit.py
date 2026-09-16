from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from backend.fonts.embed import inject_fonts
from backend.ingest.pdf import PageImage
from backend.llm.client import LLM, LLMError, LLMReply, ToolCall, image_part
from backend.prompt.build import build_unit_message
from backend.skill.bundle import Bundle
from backend.skill.manifest import get_reference
from backend.verify.self_check import CheckResult, run_self_check

MAX_CALLS = 3
RETRY_BACKOFF_BASE_SECONDS = 0.1
RETRY_BACKOFF_MAX_SECONDS = 1.0


class UnitStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    VERIFYING = "verifying"
    REPAIRING = "repairing"
    OK = "ok"
    NEEDS_ATTENTION = "needs-attention"
    FAILED = "failed"


@dataclass(frozen=True)
class UnitRequest:
    unit_id: str
    label: str
    page_numbers: list[int]
    pages: list[PageImage]


@dataclass(frozen=True)
class UnitResult:
    status: UnitStatus
    calls: int
    requested_refs: list[str] = field(default_factory=list)
    artifact_path: Path | None = None
    findings: list[str] = field(default_factory=list)


def _tools(bundle: Bundle) -> list[dict]:
    names = sorted(bundle.references)
    listing = "; ".join(f"{name} ({bundle.references[name].title})" for name in names)
    return [
        {
            "type": "function",
            "function": {
                "name": "list_references",
                "description": "List the available design-system reference files.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_reference",
                "description": f"Read one reference. Valid names: {listing}",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": names},
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def _malformed_arguments(arguments: object) -> str | None:
    if not isinstance(arguments, dict):
        return "malformed tool arguments"
    return None


def _resolve_tool_calls(
    calls: Sequence[ToolCall], bundle: Bundle, requested: list[str]
) -> list[dict]:
    results: list[dict] = []
    for call in calls:
        if call.name not in {"list_references", "read_reference"}:
            content = f"unknown tool: {call.name!r}"
            results.append({"role": "tool", "tool_call_id": call.id, "content": content})
            continue

        try:
            arguments = json.loads(call.arguments or "{}")
        except (json.JSONDecodeError, TypeError):
            content = "malformed tool arguments"
        else:
            content = _malformed_arguments(arguments)
            if content is None and call.name == "list_references":
                content = "\n".join(
                    f"{key}: {reference.title} — {reference.summary}"
                    for key, reference in sorted(bundle.references.items())
                )
            elif content is None:
                name = arguments.get("name")
                if not isinstance(name, str) or not name:
                    content = "malformed tool arguments: read_reference requires a name"
                else:
                    try:
                        reference = get_reference(bundle.references, name)
                    except KeyError:
                        content = f"unknown reference: {name!r}. Use list_references."
                    else:
                        requested.append(name)
                        try:
                            content = reference.path.read_text(encoding="utf-8")
                        except OSError as error:
                            content = f"reference unavailable: {error}"
        results.append({"role": "tool", "tool_call_id": call.id, "content": content})
    return results


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped + "\n"


def _write_atomic(path: Path, text: str) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(text)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _repair_prompt(findings: Sequence[str]) -> str:
    details = "\n".join(f"- {finding}" for finding in findings)
    return (
        "The artifact failed verification:\n"
        f"{details}\n\n"
        "Return the corrected complete HTML document only. Preserve the offline and "
        "accessible SVG requirements from the original request."
    )


def _backoff_seconds(call_number: int) -> float:
    return min(
        RETRY_BACKOFF_BASE_SECONDS * (2 ** max(call_number - 1, 0)),
        RETRY_BACKOFF_MAX_SECONDS,
    )


def _failed_result(
    status: UnitStatus,
    calls: int,
    requested: list[str],
    artifact_path: Path,
    published: bool,
    findings: Sequence[str],
) -> UnitResult:
    return UnitResult(
        status,
        calls,
        list(requested),
        artifact_path if published else None,
        list(findings),
    )


def generate_unit(
    request: UnitRequest,
    *,
    llm: LLM,
    bundle: Bundle,
    fonts_css: str,
    out_dir: Path,
    max_calls: int = MAX_CALLS,
    checker: Callable[[Path, Path], CheckResult] = run_self_check,
) -> UnitResult:
    budget = max(0, min(MAX_CALLS, max_calls))
    calls = 0
    requested: list[str] = []
    last_findings: list[str] = []
    artifacts = out_dir / "units" / request.unit_id
    artifacts.mkdir(parents=True, exist_ok=True)
    artifact_path = artifacts / "artifact.html"
    published = False

    content: list[dict] = [image_part(page) for page in request.pages]
    content.append(
        {"type": "text", "text": build_unit_message(request.label, request.page_numbers)}
    )
    messages: list[dict] = [
        {"role": "system", "content": bundle.system_prompt},
        {"role": "user", "content": content},
    ]

    while calls < budget:
        calls += 1
        try:
            reply: LLMReply = llm.complete(messages, tools=_tools(bundle))
        except LLMError as error:
            error_finding = str(error)
            if not error.retryable or calls >= budget:
                findings = [*last_findings, error_finding]
                return _failed_result(
                    UnitStatus.FAILED,
                    calls,
                    requested,
                    artifact_path,
                    published,
                    findings,
                )
            time.sleep(_backoff_seconds(calls))
            continue

        if reply.tool_calls:
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

        html = _strip_fences(reply.text or "")
        if fonts_css:
            html = inject_fonts(html, fonts_css)
        _write_atomic(artifact_path, html)
        published = True
        result = checker(artifact_path, bundle.skill_dir)
        if result.ok:
            return UnitResult(UnitStatus.OK, calls, list(requested), artifact_path, [])

        last_findings = list(result.findings)
        if calls >= budget:
            return _failed_result(
                UnitStatus.NEEDS_ATTENTION,
                calls,
                requested,
                artifact_path,
                published,
                last_findings,
            )

        messages.append({"role": "assistant", "content": reply.text or ""})
        messages.append({"role": "user", "content": _repair_prompt(last_findings)})

    return _failed_result(
        UnitStatus.NEEDS_ATTENTION,
        calls,
        requested,
        artifact_path,
        published,
        last_findings or ["call budget exhausted before an artifact was returned"],
    )
