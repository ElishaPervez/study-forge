from __future__ import annotations

import json
import re
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pymupdf
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.fonts.embed import default_fonts_css
from backend.generate.guide import GuideRequest, generate_guide
from backend.generate.revision import RevisionRequest, revise_guide
from backend.generate.unit import UnitStatus
from backend.ingest.pdf import preview_page
from backend.ingest.source import (
    IMAGE_MEDIA_TYPES,
    SOURCE_MANIFEST_NAME,
    source_dir_for,
    store_source,
    validate_stored_source,
)
from backend.jobs.schema import GuideRecord, LegacyHistoryEntry, SourceAsset
from backend.jobs.store import (
    delete_guide,
    guide_dir_for,
    list_guides,
    list_history,
    load_guide,
    new_guide,
    recover_interrupted_guides,
    update_guide,
    write_atomic,
)
from backend.llm.client import LLM, OpenRouterLLM
from backend.settings import Settings
from backend.skill.bundle import load_bundle


class RegisterSourceBody(BaseModel):
    paths: list[str]


class CreateGuideBody(BaseModel):
    source_id: str
    selection: dict[str, object]


class RenameGuideBody(BaseModel):
    name: str


class RevisionBody(BaseModel):
    selected_text: str
    instruction: str
    mode: Literal["clarify", "custom"]


WINDOWS_RESERVED_EXPORT_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}

SOURCE_RECOVERY_MESSAGE = (
    "The stored source could not be read. Choose the source again in the Source section."
)


class _SourceUnavailableError(HTTPException):
    def __init__(self, diagnostic: str) -> None:
        super().__init__(status_code=404, detail=SOURCE_RECOVERY_MESSAGE)
        self.diagnostic = diagnostic


def _is_source_read_failure(error: BaseException) -> bool:
    return isinstance(error, (FileNotFoundError, pymupdf.FileDataError, HTTPException))


def _error_diagnostic(error: BaseException) -> str:
    diagnostic = getattr(error, "diagnostic", None)
    if isinstance(diagnostic, str) and diagnostic:
        return diagnostic
    if isinstance(error, HTTPException):
        detail = error.detail
        if isinstance(detail, str) and detail:
            return detail
    return str(error) or error.__class__.__name__


def create_app(settings: Settings, llm: LLM | None = None) -> FastAPI:
    app = FastAPI(title="Study Forge")
    app.state.started_at = datetime.now(UTC)
    bundle = load_bundle(settings.skill_dir)
    fonts_css = default_fonts_css()
    app.state.settings = settings
    app.state.llm = llm or OpenRouterLLM(
        api_key=settings.openrouter_api_key,
        model=settings.model,
        reasoning_effort=settings.reasoning_effort,
        max_output_tokens=settings.max_output_tokens,
    )
    guide_locks: dict[str, threading.Lock] = {}
    guide_locks_guard = threading.Lock()
    source_operations_lock = threading.Lock()

    def _guide_lock(guide_id: str) -> threading.Lock:
        with guide_locks_guard:
            return guide_locks.setdefault(guide_id, threading.Lock())

    def _source_or_404(source_id: str) -> SourceAsset:
        try:
            source_dir = source_dir_for(settings.jobs_dir, source_id)
            for manifest_name in (SOURCE_MANIFEST_NAME, "manifest.json"):
                manifest_path = source_dir / manifest_name
                if not manifest_path.is_file():
                    continue
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                source = SourceAsset.from_dict(payload)
                if source.source_id != source_id:
                    raise ValueError("source manifest id does not match its directory")
                try:
                    validate_stored_source(source, source_dir)
                except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
                    raise _SourceUnavailableError(str(error)) from None
                return source
            raise FileNotFoundError
        except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
            raise HTTPException(status_code=404, detail="unknown source") from None

    def _guide_or_404(guide_id: str) -> GuideRecord:
        recover_interrupted_guides(settings.jobs_dir, app.state.started_at)
        try:
            return load_guide(settings.jobs_dir, guide_id)
        except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
            raise HTTPException(status_code=404, detail="unknown guide") from None

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
        artifact_path = guide_dir_for(settings.jobs_dir, guide.guide_id) / "artifact.html"
        view = guide.to_dict()
        try:
            source = _source_or_404(guide.source_id)
        except HTTPException:
            view["source"] = None
            view["source_error"] = SOURCE_RECOVERY_MESSAGE
        else:
            view["source"] = _source_view(source)
        view["artifact_url"] = (
            f"/api/guides/{guide.guide_id}/artifact.html"
            if guide.status == UnitStatus.OK.value and artifact_path.is_file()
            else None
        )
        return view

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

    def _generation_artifact(
        guide: GuideRecord, candidate: Path | None, status: UnitStatus
    ) -> Path | None:
        if status != UnitStatus.OK or candidate is None or not candidate.is_file():
            return None
        target = guide_dir_for(settings.jobs_dir, guide.guide_id) / "artifact.html"
        write_atomic(target, candidate.read_bytes())
        return target

    def _run_generation(guide: GuideRecord, *, retry: bool) -> dict:
        lock = _guide_lock(guide.guide_id)
        with lock:
            current = _guide_or_404(guide.guide_id)
            return _run_generation_locked(current, retry=retry)

    def _run_generation_locked(guide: GuideRecord, *, retry: bool) -> dict:
        if not retry and guide.status == UnitStatus.OK.value:
            artifact_path = guide_dir_for(settings.jobs_dir, guide.guide_id) / "artifact.html"
            if artifact_path.is_file():
                return _guide_view(guide)
        if retry and guide.status not in {
            UnitStatus.FAILED.value,
            UnitStatus.NEEDS_ATTENTION.value,
        }:
            raise HTTPException(status_code=409, detail="only a failed guide can be retried")
        if not retry and guide.status != UnitStatus.PENDING.value:
            raise HTTPException(status_code=409, detail="guide is not pending")

        current = update_guide(
            settings.jobs_dir,
            guide,
            status=UnitStatus.RUNNING.value,
            error=None,
            findings=[],
        )

        def save_status(status: UnitStatus) -> None:
            nonlocal current
            current = update_guide(settings.jobs_dir, current, status=status.value)

        try:
            source = _source_or_404(current.source_id)
            result = generate_guide(
                GuideRequest(current.guide_id, source, current.selection),
                source_root=settings.jobs_dir,
                llm=app.state.llm,
                bundle=bundle,
                fonts_css=fonts_css,
                out_dir=guide_dir_for(settings.jobs_dir, current.guide_id),
                status_callback=save_status,
            )
            _generation_artifact(current, result.artifact_path, result.status)
            status = result.status.value
            error = None if status == UnitStatus.OK.value else "; ".join(result.findings)
            if status != UnitStatus.OK.value and not error:
                error = "guide generation did not produce a verified artifact"
            name = result.name if result.status == UnitStatus.OK else current.name
            current = update_guide(
                settings.jobs_dir,
                current,
                name=name,
                status=status,
                error=error,
                findings=result.findings,
            )
        except Exception as error:  # noqa: BLE001 - every generation failure must persist
            message = SOURCE_RECOVERY_MESSAGE if _is_source_read_failure(error) else _error_diagnostic(error)
            current = update_guide(
                settings.jobs_dir,
                current,
                status=UnitStatus.FAILED.value,
                error=message,
                findings=[_error_diagnostic(error)],
            )
        return _guide_view(current)

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
            detail = SOURCE_RECOVERY_MESSAGE if _is_source_read_failure(error) else str(error)
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
        with source_operations_lock:
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

    @app.post("/api/guides")
    def create_guide(body: CreateGuideBody) -> dict:
        with source_operations_lock:
            source = _source_or_404(body.source_id)
            try:
                guide = new_guide(settings.jobs_dir, source, body.selection)
            except (TypeError, ValueError) as error:
                raise HTTPException(status_code=400, detail=str(error)) from None
            return _guide_view(guide)

    @app.get("/api/guides")
    def get_guides() -> list[dict]:
        return [
            entry.to_dict() if isinstance(entry, LegacyHistoryEntry) else _guide_view(entry)
            for entry in list_history(settings.jobs_dir, recover_before=app.state.started_at)
        ]

    @app.get("/api/guides/{guide_id}")
    def get_guide(guide_id: str) -> dict:
        return _guide_view(_guide_or_404(guide_id))

    @app.post("/api/guides/{guide_id}/generate")
    def generate(guide_id: str) -> dict:
        return _run_generation(_guide_or_404(guide_id), retry=False)

    @app.post("/api/guides/{guide_id}/retry")
    def retry(guide_id: str) -> dict:
        return _run_generation(_guide_or_404(guide_id), retry=True)

    @app.patch("/api/guides/{guide_id}")
    def rename_guide(guide_id: str, body: RenameGuideBody) -> dict:
        with _guide_lock(guide_id):
            guide = _guide_or_404(guide_id)
            name = body.name.strip()
            if name:
                guide = update_guide(settings.jobs_dir, guide, name=name)
            return _guide_view(guide)

    @app.delete("/api/guides/{guide_id}")
    def remove_guide(guide_id: str) -> dict:
        with _guide_lock(guide_id):
            guide = _guide_or_404(guide_id)
            with source_operations_lock:
                delete_guide(settings.jobs_dir, guide.guide_id)
            return {"guide_id": guide.guide_id, "deleted": True}

    @app.get("/api/guides/{guide_id}/artifact.html")
    def artifact(guide_id: str, download: int = 0) -> FileResponse:
        guide = _guide_or_404(guide_id)
        if download not in {0, 1}:
            raise HTTPException(status_code=400, detail="download must be 0 or 1")
        if guide.status != UnitStatus.OK.value:
            raise HTTPException(status_code=409, detail="only a verified guide has an artifact")
        path = guide_dir_for(settings.jobs_dir, guide.guide_id) / "artifact.html"
        if not path.is_file():
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

    def _revise_locked(guide: GuideRecord, body: RevisionBody) -> dict:
        if not body.selected_text.strip():
            raise HTTPException(status_code=400, detail="selected text is required")
        if body.mode == "custom" and not body.instruction.strip():
            raise HTTPException(status_code=400, detail="custom revisions require an instruction")
        artifact_path = guide_dir_for(settings.jobs_dir, guide.guide_id) / "artifact.html"
        if not artifact_path.is_file():
            raise HTTPException(status_code=409, detail="guide has no generated artifact")
        if guide.status != UnitStatus.OK.value:
            raise HTTPException(status_code=409, detail="only a verified guide can be revised")
        try:
            current_html = artifact_path.read_text(encoding="utf-8")
            source = _source_or_404(guide.source_id)
            result = revise_guide(
                RevisionRequest(
                    guide.guide_id,
                    source,
                    guide.selection,
                    body.selected_text,
                    body.instruction,
                    body.mode,
                    current_html,
                ),
                source_root=settings.jobs_dir,
                llm=app.state.llm,
                bundle=bundle,
                fonts_css=fonts_css,
            )
        except (FileNotFoundError, HTTPException, OSError, RuntimeError, ValueError) as error:
            message = SOURCE_RECOVERY_MESSAGE if _is_source_read_failure(error) else str(error)
            raise HTTPException(status_code=400, detail=message) from None
        if result.html is None:
            raise HTTPException(
                status_code=400,
                detail={"message": "revision failed", "findings": result.findings},
            )
        write_atomic(artifact_path, result.html.encode("utf-8"))
        guide = update_guide(
            settings.jobs_dir,
            guide,
            revision_count=guide.revision_count + 1,
            error=None,
        )
        return _guide_view(guide)

    @app.post("/api/guides/{guide_id}/revisions")
    def revise(guide_id: str, body: RevisionBody) -> dict:
        with _guide_lock(guide_id):
            guide = _guide_or_404(guide_id)
            return _revise_locked(guide, body)

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
