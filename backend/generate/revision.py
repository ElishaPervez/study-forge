from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from backend.fonts.embed import inject_fonts
from backend.generate.unit import (
    MAX_CALLS,
    _backoff_seconds,
    _repair_prompt,
    _resolve_tool_calls,
    _strip_fences,
    _tools,
    _v1_output_policy_findings,
)
from backend.ingest.source import source_inputs
from backend.jobs.schema import Selection, SourceAsset
from backend.llm.client import LLM, LLMError, LLMReply, image_part
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
) -> RevisionResult:
    inputs = source_inputs(request.source, request.selection, source_root)
    content = [image_part(image) for image in inputs]
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

    while calls < budget:
        calls += 1
        try:
            reply: LLMReply = llm.complete(messages, tools=_tools(bundle))
        except LLMError as error:
            if not error.retryable or calls >= budget:
                return RevisionResult(None, calls, list(requested), [*last_findings, str(error)])
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

        candidate = inject_fonts(_strip_fences(reply.text or ""), fonts_css)
        findings = _validated_findings(candidate, bundle.skill_dir, checker)
        if not findings:
            return RevisionResult(candidate, calls, list(requested), [])

        last_findings = findings
        if repair_used or calls >= budget:
            return RevisionResult(None, calls, list(requested), last_findings)

        repair_used = True
        messages.append({"role": "assistant", "content": reply.text or ""})
        messages.append({"role": "user", "content": _repair_prompt(last_findings)})

    return RevisionResult(
        None,
        calls,
        list(requested),
        last_findings or ["call budget exhausted before a valid revision was returned"],
    )


def _validated_findings(
    html: str, skill_dir: Path, checker: Callable[[Path, Path], CheckResult]
) -> list[str]:
    policy_findings = _v1_output_policy_findings(html)
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
        result = checker(candidate_path, skill_dir)
    finally:
        if candidate_path is not None:
            candidate_path.unlink(missing_ok=True)
    if policy_findings:
        return [*policy_findings, *result.findings]
    if not result.ok:
        return result.findings or ["artifact checker rejected the candidate without findings"]
    return []
