"""The local API.

Every handler here is quick: it validates the action, records it, and answers.
Slow work (source reads, model calls, checking an artifact) runs in the single
background queue, which publishes a guide only after the result is checked.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.diagnostics.trace import logs_dir_for, record_api_call, trace_for
from backend.fonts.embed import default_fonts_css
from backend.generate.runner import (
    SOURCE_RECOVERY_MESSAGE,
    GuideRunner,
    is_source_read_failure,
)
from backend.generate.unit import UnitStatus
from backend.ingest.pdf import preview_page
from backend.ingest.source import (
    IMAGE_MEDIA_TYPES,
    SOURCE_MANIFEST_NAME,
    source_dir_for,
    store_source,
    validate_stored_source,
)
from backend.jobs.operations import (
    completed_file_path,
    current_fingerprint,
    guide_id_for_receipt,
    recover_operations,
)
from backend.jobs.queue import GuideQueue, QueueError, QueueRequest
from backend.jobs.schema import (
    GuideRecord,
    LegacyHistoryEntry,
    OperationRecord,
    SourceAsset,
)
from backend.jobs.store import (
    delete_guide,
    list_guides,
    list_history,
    load_guide,
    new_guide,
    update_guide,
)
from backend.llm.client import LLM, OpenRouterLLM
from backend.settings import Settings
from backend.skill.bundle import load_bundle


class RegisterSourceBody(BaseModel):
    paths: list[str]


class CreateGuideBody(BaseModel):
    source_id: str
    selection: dict[str, object]
    receipt: str


class RenameGuideBody(BaseModel):
    name: str


class OperationBody(BaseModel):
    receipt: str


class RevisionBody(BaseModel):
    selected_text: str
    instruction: str = ""
    mode: Literal["clarify", "custom"]
    receipt: str


WINDOWS_RESERVED_EXPORT_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class _SourceUnavailableError(Exception):
    """A stored source that cannot be read, without HTTP semantics."""

    def __init__(self, diagnostic: str, *, known: bool = True) -> None:
        super().__init__(diagnostic)
        self.diagnostic = diagnostic
        # A source with no manifest at all is simply unknown; an existing one that
        # cannot be read tells the user to choose the source again.
        self.known = known


def create_app(settings: Settings, llm: LLM | None = None) -> FastAPI:
    # Decide which saved versions are authoritative once, before any screen reads
    # history: a request that was still unfinished belongs to an earlier service.
    recover_operations(settings.jobs_dir)
    queues: list[GuideQueue] = []

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        for queue in queues:
            queue.stop()

    app = FastAPI(title="Study Forge", lifespan=lifespan)

    @app.middleware("http")
    async def _timed_request(request: Request, call_next):
        """Every HTTP reply is timed, so the click-to-accepted path is measurable."""
        started = time.perf_counter()
        response = await call_next(request)
        record_api_call(
            logs_dir_for(settings.jobs_dir),
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            # The queue panel polls while work runs; the flag keeps it out of the
            # way of the requests a person actually caused.
            poll=request.url.path == "/api/queue",
        )
        return response

    bundle = load_bundle(settings.skill_dir)
    fonts_css = default_fonts_css()
    app.state.settings = settings
    app.state.llm = llm or OpenRouterLLM(
        api_key=settings.openrouter_api_key,
        model=settings.model,
        reasoning_effort=settings.reasoning_effort,
        max_output_tokens=settings.max_output_tokens,
    )
    source_operations_lock = threading.Lock()

    def _load_source(source_id: str) -> SourceAsset:
        """Read and validate a stored source; the caller decides what a failure means."""
        try:
            source_dir = source_dir_for(settings.jobs_dir, source_id)
        except (OSError, ValueError):
            raise _SourceUnavailableError(f"invalid source id: {source_id!r}") from None
        for manifest_name in (SOURCE_MANIFEST_NAME, "manifest.json"):
            manifest_path = source_dir / manifest_name
            if not manifest_path.is_file():
                continue
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                source = SourceAsset.from_dict(payload)
                if source.source_id != source_id:
                    raise ValueError("source manifest id does not match its directory")
                validate_stored_source(source, source_dir)
            except (
                FileNotFoundError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ) as error:
                raise _SourceUnavailableError(str(error)) from None
            return source
        raise _SourceUnavailableError(
            f"no stored manifest for source {source_id!r}", known=False
        )

    def _source_or_404(source_id: str) -> SourceAsset:
        try:
            return _load_source(source_id)
        except _SourceUnavailableError as error:
            detail = SOURCE_RECOVERY_MESSAGE if error.known else "unknown source"
            raise HTTPException(status_code=404, detail=detail) from None

    def _guide_or_404(guide_id: str) -> GuideRecord:
        try:
            return load_guide(settings.jobs_dir, guide_id)
        except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
            raise HTTPException(status_code=404, detail="unknown guide") from None

    def _artifact_path(guide: GuideRecord) -> Path | None:
        try:
            return completed_file_path(settings.jobs_dir, guide)
        except ValueError:
            return None

    def _source_view(source: SourceAsset, **state: object) -> dict:
        source_dir = source_dir_for(settings.jobs_dir, source.source_id)
        details: list[dict[str, object]] = []
        for stored_name in source.files:
            path = source_dir / stored_name
            try:
                stat = path.stat()
            except OSError:
                details.append(
                    {
                        "name": stored_name,
                        "size": None,
                        "exists": False,
                    }
                )
            else:
                details.append(
                    {
                        "name": stored_name,
                        "size": stat.st_size,
                        "exists": True,
                    }
                )
        view = source.to_dict()
        view["file_details"] = details
        view.update(state)
        return view

    def _guide_view(guide: GuideRecord) -> dict:
        view = guide.to_dict()
        try:
            source = _load_source(guide.source_id)
        except _SourceUnavailableError:
            view["source"] = None
            view["source_error"] = SOURCE_RECOVERY_MESSAGE
        else:
            view["source"] = _source_view(source)
        artifact_path = _artifact_path(guide)
        view["artifact_url"] = (
            f"/api/guides/{guide.guide_id}/artifact.html"
            if guide.status == UnitStatus.OK.value
            and artifact_path is not None
            and artifact_path.is_file()
            else None
        )
        active = queue.current_operation(guide.guide_id)
        view["operation"] = queue.row(active.receipt) if active is not None else None
        retryable = queue.retryable_failure(guide.guide_id)
        view["retryable_request"] = retryable.summary() if retryable is not None else None
        return view

    def _request_receipt(value: str) -> str:
        """A request receipt must be well formed before it names anything."""
        try:
            guide_id_for_receipt(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="invalid request receipt") from None
        return value

    def _submit(request: QueueRequest) -> OperationRecord:
        try:
            return queue.submit(request)
        except QueueError as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from None

    def _accepted_view(guide: GuideRecord, operation: OperationRecord) -> dict:
        view = _guide_view(guide)
        row = queue.row(operation.receipt)
        if row is not None:
            view["operation"] = row
        return view

    def _record_acceptance(
        started: float, operation: OperationRecord, **fields: object
    ) -> None:
        """Note in the request's own trace when the service accepted it."""
        trace_for(settings.jobs_dir, operation.receipt).event(
            "api.accepted",
            elapsed_ms=round((time.perf_counter() - started) * 1000.0, 1),
            guide_id=operation.guide_id,
            **fields,
        )

    def _operation_row(receipt: str) -> dict:
        row = queue.row(_request_receipt(receipt))
        if row is None:
            raise HTTPException(status_code=404, detail="unknown request")
        return row

    def _source_kind(paths: list[Path]) -> Literal["pdf", "images"]:
        if not paths:
            raise ValueError("at least one source path is required")
        suffixes = {path.suffix.casefold() for path in paths}
        if ".pdf" in suffixes:
            if len(paths) != 1 or suffixes != {".pdf"}:
                raise ValueError("a source must be one PDF or one or more images")
            return "pdf"
        unsupported = sorted(suffix for suffix in suffixes if suffix not in IMAGE_MEDIA_TYPES)
        if unsupported:
            raise ValueError(f"unsupported source format: {unsupported[0] or '<none>'}")
        return "images"

    runner = GuideRunner(
        jobs_dir=settings.jobs_dir,
        llm=lambda: app.state.llm,
        bundle=bundle,
        fonts_css=fonts_css,
        load_source=_load_source,
    )
    queue = GuideQueue(jobs_dir=settings.jobs_dir, runner=runner)
    queue.start()
    queues.append(queue)
    app.state.queue = queue

    @app.post("/api/sources")
    def register_source(body: RegisterSourceBody) -> dict:
        paths = [Path(path) for path in body.paths]
        with source_operations_lock:
            try:
                kind = _source_kind(paths)
                source = store_source(settings.jobs_dir, paths, kind)
            except (FileNotFoundError, OSError, ValueError, RuntimeError) as error:
                raise HTTPException(status_code=400, detail=str(error)) from None
            return _source_view(source)

    @app.get("/api/sources/{source_id}")
    def get_source(source_id: str) -> dict:
        return _source_view(_source_or_404(source_id))

    @app.get("/api/sources/{source_id}/pages/{page_number}")
    def source_page(source_id: str, page_number: int) -> FileResponse:
        source = _source_or_404(source_id)
        if source.kind != "pdf":
            raise HTTPException(status_code=400, detail="page previews are available for PDFs only")
        if page_number < 1 or (source.page_count is not None and page_number > source.page_count):
            raise HTTPException(status_code=404, detail="unknown source page")
        if not source.files:
            raise HTTPException(status_code=404, detail="stored PDF is unavailable")
        source_path = source_dir_for(settings.jobs_dir, source.source_id) / source.files[0]
        try:
            rendered = preview_page(source_path, page_number, source_path.parent / "previews")
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
            detail = SOURCE_RECOVERY_MESSAGE if is_source_read_failure(error) else str(error)
            raise HTTPException(status_code=404, detail=detail) from None
        return FileResponse(rendered.path, media_type="image/jpeg")

    @app.get("/api/sources/{source_id}/images/{image_number}")
    def source_image(source_id: str, image_number: int) -> FileResponse:
        source = _source_or_404(source_id)
        if source.kind != "images":
            raise HTTPException(status_code=400, detail="image previews are available for image sources only")
        if image_number < 1 or image_number > len(source.files):
            raise HTTPException(status_code=404, detail="unknown source image")
        path = source_dir_for(settings.jobs_dir, source.source_id) / source.files[image_number - 1]
        if not path.is_file():
            raise HTTPException(status_code=404, detail="source image is unavailable")
        try:
            media_type = IMAGE_MEDIA_TYPES[path.suffix.casefold()]
        except KeyError:
            raise HTTPException(status_code=404, detail="unsupported source image") from None
        return FileResponse(path, media_type=media_type)

    @app.delete("/api/sources/{source_id}")
    def remove_source(source_id: str) -> dict:
        # The reference check and source removal share admission with guide
        # acceptance, so a new guide cannot be created against a source while it
        # is being removed.
        with queue.admission(), source_operations_lock:
            try:
                source_dir = source_dir_for(settings.jobs_dir, source_id)
            except (OSError, ValueError):
                raise HTTPException(status_code=404, detail="unknown source") from None
            if not source_dir.is_dir():
                raise HTTPException(status_code=404, detail="unknown source")
            referenced = any(
                guide.source_id == source_id for guide in list_guides(settings.jobs_dir)
            )
            if referenced:
                try:
                    source = _source_or_404(source_id)
                except HTTPException:
                    return {
                        "source_id": source_id,
                        "deleted": False,
                        "retained": True,
                    }
                return _source_view(source, deleted=False, retained=True)
            shutil.rmtree(source_dir)
            return {"source_id": source_id, "deleted": True, "retained": False}

    @app.post("/api/guides", status_code=202)
    def create_guide(body: CreateGuideBody) -> dict:
        started = time.perf_counter()
        guide_id = _request_receipt(body.receipt)

        def prepare() -> None:
            # Runs under admission, and under the source lock, so the source
            # cannot be removed between the check and the new history entry.
            with source_operations_lock:
                source = _source_or_404(body.source_id)
                try:
                    created = new_guide(
                        settings.jobs_dir, source, body.selection, guide_id=guide_id
                    )
                except (TypeError, ValueError) as error:
                    raise HTTPException(status_code=400, detail=str(error)) from None
                queue.remember_guide(created.guide_id, created.name)

        operation = _submit(
            QueueRequest(
                guide_id=guide_id,
                kind="create",
                receipt=guide_id,
                prepare=prepare,
            )
        )
        _record_acceptance(
            started,
            operation,
            endpoint="POST /api/guides",
            kind="create",
            source_id=body.source_id,
            selection=body.selection,
        )

        return _accepted_view(_guide_or_404(guide_id), operation)

    @app.get("/api/guides")
    def get_guides() -> list[dict]:
        return [
            entry.to_dict() if isinstance(entry, LegacyHistoryEntry) else _guide_view(entry)
            for entry in list_history(settings.jobs_dir)
        ]

    @app.get("/api/guides/{guide_id}")
    def get_guide(guide_id: str) -> dict:
        return _guide_view(_guide_or_404(guide_id))

    @app.post("/api/guides/{guide_id}/generate", status_code=202)
    def generate(guide_id: str, body: OperationBody) -> dict:
        started = time.perf_counter()
        receipt = _request_receipt(body.receipt)
        guide = _guide_or_404(guide_id)
        if guide.status == UnitStatus.OK.value and _artifact_path(guide) is not None:
            raise HTTPException(status_code=409, detail="guide is already complete")
        if guide.status != UnitStatus.PENDING.value:
            raise HTTPException(status_code=409, detail="guide is not pending")
        operation = _submit(
            QueueRequest(guide_id=guide.guide_id, kind="create", receipt=receipt)
        )
        _record_acceptance(
            started, operation, endpoint="POST /api/guides/{guide}/generate", kind="create"
        )
        return _accepted_view(guide, operation)

    @app.post("/api/guides/{guide_id}/retry", status_code=202)
    def retry(guide_id: str, body: OperationBody) -> dict:
        started = time.perf_counter()
        receipt = _request_receipt(body.receipt)
        guide = _guide_or_404(guide_id)
        if guide.status not in {UnitStatus.FAILED.value, UnitStatus.NEEDS_ATTENTION.value}:
            raise HTTPException(status_code=409, detail="only a failed guide can be retried")
        operation = _submit(
            QueueRequest(guide_id=guide.guide_id, kind="retry", receipt=receipt)
        )
        _record_acceptance(
            started, operation, endpoint="POST /api/guides/{guide}/retry", kind="retry"
        )
        return _accepted_view(guide, operation)

    @app.post("/api/guides/{guide_id}/revisions", status_code=202)
    def revise(guide_id: str, body: RevisionBody) -> dict:
        started = time.perf_counter()
        receipt = _request_receipt(body.receipt)
        if not body.selected_text.strip():
            raise HTTPException(status_code=400, detail="selected text is required")
        if body.mode == "custom" and not body.instruction.strip():
            raise HTTPException(status_code=400, detail="custom revisions require an instruction")
        guide = _guide_or_404(guide_id)
        if guide.status != UnitStatus.OK.value:
            raise HTTPException(status_code=409, detail="only a verified guide can be revised")
        base_fingerprint = current_fingerprint(settings.jobs_dir, guide)
        if base_fingerprint is None:
            raise HTTPException(status_code=409, detail="guide has no generated artifact")
        operation = _submit(
            QueueRequest(
                guide_id=guide.guide_id,
                kind="clarify" if body.mode == "clarify" else "update",
                receipt=receipt,
                selected_text=body.selected_text,
                instruction=body.instruction,
                revision_mode=body.mode,
                intended_revision=guide.revision_count,
                base_fingerprint=base_fingerprint,
            )
        )
        _record_acceptance(
            started,
            operation,
            endpoint="POST /api/guides/{guide}/revisions",
            kind="clarify" if body.mode == "clarify" else "update",
            mode=body.mode,
            selected_chars=len(body.selected_text),
            instruction_chars=len(body.instruction),
        )
        return _accepted_view(guide, operation)

    @app.patch("/api/guides/{guide_id}")
    def rename_guide(guide_id: str, body: RenameGuideBody) -> dict:
        with queue.admission():
            guide = _guide_or_404(guide_id)
            if queue.guide_is_busy(guide_id):
                raise HTTPException(
                    status_code=409,
                    detail="this guide already has a request waiting or running",
                )
            name = body.name.strip()
            if name:
                guide = update_guide(settings.jobs_dir, guide, name=name)
            queue.remember_guide(guide.guide_id, guide.name)
            return _guide_view(guide)

    @app.delete("/api/guides/{guide_id}")
    def remove_guide(guide_id: str) -> dict:
        with queue.admission():
            guide = _guide_or_404(guide_id)
            if queue.guide_is_busy(guide_id):
                raise HTTPException(
                    status_code=409,
                    detail="this guide already has a request waiting or running",
                )
            with source_operations_lock:
                delete_guide(settings.jobs_dir, guide.guide_id)
            queue.mark_guide_deleted(guide.guide_id)
            return {"guide_id": guide.guide_id, "deleted": True}

    @app.get("/api/guides/{guide_id}/artifact.html")
    def artifact(guide_id: str, download: int = 0) -> FileResponse:
        guide = _guide_or_404(guide_id)
        if download not in {0, 1}:
            raise HTTPException(status_code=400, detail="download must be 0 or 1")
        if guide.status != UnitStatus.OK.value:
            raise HTTPException(status_code=409, detail="only a verified guide has an artifact")
        path = _artifact_path(guide)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="artifact not generated yet")
        disposition = "attachment" if download else "inline"
        return FileResponse(
            path,
            media_type="text/html; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'{disposition}; filename="{_export_filename(guide)}"'
                )
            },
        )

    @app.get("/api/queue")
    def queue_summary() -> dict:
        return queue.snapshot()

    @app.get("/api/operations/{receipt}")
    def operation_view(receipt: str) -> dict:
        return _operation_row(receipt)

    @app.post("/api/operations/{receipt}/retry", status_code=202)
    def retry_operation(receipt: str, body: OperationBody) -> dict:
        started = time.perf_counter()
        original = _operation_row(receipt)
        new_receipt = _request_receipt(body.receipt)
        try:
            operation = queue.retry_request(original["receipt"], new_receipt=new_receipt)
        except QueueError as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from None
        _record_acceptance(
            started,
            operation,
            endpoint="POST /api/operations/{receipt}/retry",
            kind=operation.kind,
            retried_receipt=receipt,
        )
        return _accepted_view(_guide_or_404(operation.guide_id), operation)

    @app.post("/api/shutdown/prepare")
    def prepare_shutdown() -> dict:
        return queue.prepare_close()

    @app.post("/api/shutdown/resume")
    def resume_shutdown() -> dict:
        return queue.resume_close()

    @app.post("/api/shutdown/confirm")
    def confirm_shutdown() -> dict:
        return queue.confirm_close()

    return app


def _export_filename(guide: GuideRecord) -> str:
    normalized = guide.name.encode("ascii", "ignore").decode("ascii")
    base = re.sub(r"[^A-Za-z0-9]+", "-", normalized).strip("-") or "study-guide"
    if base.upper() in WINDOWS_RESERVED_EXPORT_NAMES:
        base = f"study-{base}"
    if guide.selection.get("mode") == "custom":
        start = guide.selection.get("start")
        end = guide.selection.get("end")
        if isinstance(start, int) and isinstance(end, int):
            base = f"{base}-pages-{start}-{end}"
    return f"{base.lower()}.html"
