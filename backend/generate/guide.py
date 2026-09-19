from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from backend.generate.unit import MAX_CALLS, UnitRequest, UnitResult, UnitStatus, generate_unit
from backend.ingest.source import source_inputs
from backend.jobs.schema import Selection, SourceAsset
from backend.llm.client import LLM
from backend.prompt.build import extract_guide_title
from backend.skill.bundle import Bundle
from backend.verify.self_check import CheckResult, run_self_check


@dataclass(frozen=True)
class GuideRequest:
    guide_id: str
    source: SourceAsset
    selection: Selection


@dataclass(frozen=True)
class GuideResult:
    status: UnitStatus
    name: str
    calls: int
    requested_refs: list[str]
    artifact_path: Path | None
    findings: list[str]


def generate_guide(
    request: GuideRequest,
    *,
    source_root: Path | None = None,
    llm: LLM,
    bundle: Bundle,
    fonts_css: str,
    out_dir: Path,
    max_calls: int = MAX_CALLS,
    checker: Callable[[Path, Path], CheckResult] = run_self_check,
    status_callback: Callable[[UnitStatus], None] | None = None,
) -> GuideResult:
    inputs = source_inputs(request.source, request.selection, source_root)
    unit_request = UnitRequest(
        request.guide_id,
        request.source.display_name,
        [image.ordinal for image in inputs],
        inputs,
        request.source.kind,
        request.selection,
    )
    result: UnitResult = generate_unit(
        unit_request,
        llm=llm,
        bundle=bundle,
        fonts_css=fonts_css,
        out_dir=out_dir,
        max_calls=max_calls,
        checker=checker,
        status_callback=status_callback,
    )
    name = "Untitled guide"
    if result.artifact_path is not None and result.artifact_path.is_file():
        name = extract_guide_title(result.artifact_path.read_text(encoding="utf-8"))
    return GuideResult(
        result.status,
        name,
        result.calls,
        result.requested_refs,
        result.artifact_path,
        result.findings,
    )
