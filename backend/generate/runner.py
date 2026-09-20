"""Run one accepted request and publish only a checked result.

The worker calls this for every accepted request. Nothing here is reached by an
HTTP handler, so a slow source read or model call can never delay the app; the
runner only ever touches the one guide its request names, and it replaces that
guide's completed version in a single write after the result is checked.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from backend.diagnostics.trace import record, span
from backend.generate.guide import GuideRequest, generate_guide
from backend.generate.revision import RevisionRequest, revise_guide
from backend.generate.unit import MAX_CALLS, STOPPED_FINDING, UnitStatus
from backend.jobs.operations import publish_completed_guide, read_completed_html
from backend.jobs.queue import OperationContext, RunOutcome
from backend.jobs.schema import GuideRecord, OperationRecord, SourceAsset
from backend.jobs.store import guide_dir_for, load_guide, update_guide
from backend.llm.client import LLM
from backend.skill.bundle import Bundle

SOURCE_RECOVERY_MESSAGE = (
    "The stored source could not be read. Choose the source again in the Source section."
)
INTERRUPTED_RUN_MESSAGE = (
    "The app stopped before this request finished. Retry to continue."
)
UNVERIFIED_RESULT_MESSAGE = "The request finished without a verified guide."


def is_source_read_failure(error: BaseException) -> bool:
    """A stored-source read failure means the source has to be chosen again."""
    if isinstance(error, (FileNotFoundError, pymupdf.FileDataError)):
        return True
    diagnostic = getattr(error, "diagnostic", None)
    if isinstance(diagnostic, str) and diagnostic:
        return True
    # The API's source loader reports source problems as an HTTP-shaped error.
    return isinstance(getattr(error, "detail", None), str) and isinstance(
        getattr(error, "status_code", None), int
    )


def error_diagnostic(error: BaseException) -> str:
    """The most useful short explanation a failure carries."""
    diagnostic = getattr(error, "diagnostic", None)
    if isinstance(diagnostic, str) and diagnostic:
        return diagnostic
    detail = getattr(error, "detail", None)
    if isinstance(detail, str) and detail:
        return detail
    return str(error) or error.__class__.__name__


@dataclass
class GuideRunner:
    """Turn one guide record plus its saved request into a completed guide."""

    jobs_dir: Path
    llm: Callable[[], LLM]
    bundle: Bundle
    fonts_css: str
    load_source: Callable[[str], SourceAsset]
    max_calls: int = MAX_CALLS

    def __call__(self, request: OperationRecord, context: OperationContext) -> RunOutcome:
        record("run.started", kind=request.kind, guide_id=request.guide_id)
        try:
            with span("run.load_guide"):
                guide = load_guide(self.jobs_dir, request.guide_id)
        except (OSError, TypeError, ValueError) as error:
            record("run.failed", stage="load_guide", error=str(error))
            return RunOutcome(False, f"the guide is no longer available: {error}")
        if request.kind in {"create", "retry"}:
            return self.run_generation(guide, request, context)
        return self.run_revision(guide, request, context)

    # -- new guides and retries -------------------------------------------

    def run_generation(
        self,
        guide: GuideRecord,
        request: OperationRecord,
        context: OperationContext,
    ) -> RunOutcome:
        current = update_guide(
            self.jobs_dir,
            guide,
            status=UnitStatus.RUNNING.value,
            error=None,
            findings=[],
        )

        def save_status(status: UnitStatus) -> None:
            nonlocal current
            current = update_guide(self.jobs_dir, current, status=status.value)

        try:
            with span("run.load_source", source_id=current.source_id) as source_span:
                source = self.load_source(current.source_id)
                source_span["kind"] = source.kind
                source_span["bytes"] = source.total_bytes
            with span(
                "run.generate_guide",
                guide_id=current.guide_id,
                images=source.image_count or source.page_count,
                selection=current.selection.get("mode"),
            ) as generation:
                result = generate_guide(
                    GuideRequest(current.guide_id, source, current.selection),
                    source_root=self.jobs_dir,
                    llm=self.llm(),
                    bundle=self.bundle,
                    fonts_css=self.fonts_css,
                    out_dir=guide_dir_for(self.jobs_dir, current.guide_id),
                    max_calls=self.max_calls,
                    status_callback=save_status,
                    progress=context.progress,
                    stop=context.stop,
                )
                generation["status"] = result.status.value
                generation["calls"] = result.calls
        except Exception as error:  # noqa: BLE001 - any failure becomes a retryable guide
            return self._record_generation_failure(current, error)

        if result.status is not UnitStatus.OK or result.artifact_path is None:
            return self._record_generation_failure(
                current, None, status=result.status.value, findings=result.findings
            )
        if not result.artifact_path.is_file():
            return self._record_generation_failure(
                current, None, findings=[*result.findings, UNVERIFIED_RESULT_MESSAGE]
            )

        html = result.artifact_path.read_bytes()
        return self._publish(current, html, request, context, name=result.name)

    def _record_generation_failure(
        self,
        guide: GuideRecord,
        error: BaseException | None = None,
        *,
        status: str | None = None,
        findings: list[str] | None = None,
    ) -> RunOutcome:
        if error is not None:
            record("run.failed", stage="generation", error=error_diagnostic(error))
            diagnostic = error_diagnostic(error)
            message = (
                SOURCE_RECOVERY_MESSAGE if is_source_read_failure(error) else diagnostic
            )
            status = UnitStatus.FAILED.value
            findings = [diagnostic]
        else:
            findings = list(findings or [])
            status = status or UnitStatus.FAILED.value
            message = "; ".join(findings) or UNVERIFIED_RESULT_MESSAGE
            record("run.failed", stage="verification", status=status, findings=len(findings) or None)
        # The guide keeps the name the user gave it: a failed request replaces nothing.
        update_guide(
            self.jobs_dir,
            guide,
            status=status,
            error=message,
            findings=findings,
        )
        return RunOutcome(False, message)

    # -- updates ----------------------------------------------------------

    def run_revision(
        self,
        guide: GuideRecord,
        request: OperationRecord,
        context: OperationContext,
    ) -> RunOutcome:
        try:
            with span("run.read_current") as current_span:
                current_html = read_completed_html(self.jobs_dir, guide)
                current_span["chars"] = len(current_html)
            with span("run.load_source", source_id=guide.source_id) as source_span:
                source = self.load_source(guide.source_id)
                source_span["kind"] = source.kind
        except Exception as error:  # noqa: BLE001 - the guide keeps its old version
            diagnostic = error_diagnostic(error)
            message = (
                SOURCE_RECOVERY_MESSAGE if is_source_read_failure(error) else diagnostic
            )
            return RunOutcome(False, message)

        with span("run.revise_guide", mode=request.revision_mode) as revision:
            result = revise_guide(
                RevisionRequest(
                    guide.guide_id,
                    source,
                    guide.selection,
                    request.selected_text or "",
                    request.instruction or "",
                    request.revision_mode or "custom",
                    current_html,
                ),
                source_root=self.jobs_dir,
                llm=self.llm(),
                bundle=self.bundle,
                fonts_css=self.fonts_css,
                max_calls=self.max_calls,
                progress=context.progress,
                stop=context.stop,
            )
            revision["calls"] = result.calls
            revision["produced"] = result.html is not None
        if result.html is None:
            message = "; ".join(result.findings) or UNVERIFIED_RESULT_MESSAGE
            return RunOutcome(False, message)
        return self._publish(
            guide, result.html.encode("utf-8"), request, context, name=guide.name
        )

    # -- publication ------------------------------------------------------

    def _publish(
        self,
        guide: GuideRecord,
        html: bytes,
        request: OperationRecord,
        context: OperationContext,
        *,
        name: str,
    ) -> RunOutcome:
        if context.stop.is_set():
            self._record_stop(guide)
            return RunOutcome(False, STOPPED_FINDING)
        # Briefly take the same admission protection a confirmed shutdown takes,
        # then recheck that this request is still allowed to publish. The guide
        # learns about the new version in one write, after the bytes are on disk.
        with context.queue.publication() as allowed:
            if not allowed or context.stop.is_set():
                self._record_stop(guide)
                return RunOutcome(False, INTERRUPTED_RUN_MESSAGE)
            revisions = guide.revision_count
            if request.kind in {"clarify", "update"}:
                revisions = request.intended_revision + 1
            with span("run.publish", bytes=len(html), guide_name=name):
                publish_completed_guide(
                    self.jobs_dir,
                    guide,
                    html=html,
                    receipt=request.receipt,
                    revision_count=revisions,
                    name=name,
                )
            record("run.published", bytes=len(html), guide_name=name)
        return RunOutcome(True)

    def _record_stop(self, guide: GuideRecord) -> None:
        try:
            current = load_guide(self.jobs_dir, guide.guide_id)
        except (OSError, TypeError, ValueError):
            return
        if current.status == UnitStatus.OK.value:
            # An update that lost the race to a shutdown keeps its previous version.
            return
        update_guide(
            self.jobs_dir,
            current,
            status=UnitStatus.FAILED.value,
            error=INTERRUPTED_RUN_MESSAGE,
            findings=[INTERRUPTED_RUN_MESSAGE],
        )
