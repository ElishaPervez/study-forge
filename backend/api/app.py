from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.fonts.embed import default_fonts_css
from backend.generate.unit import UnitRequest, UnitStatus, generate_unit
from backend.ingest.pdf import page_count, rasterize
from backend.jobs.store import Job, load_job, new_job, set_unit_status
from backend.llm.client import LLM, OpenRouterLLM
from backend.settings import Settings
from backend.skill.bundle import load_bundle


class CreateJobBody(BaseModel):
    pdf: str
    ranges: list[tuple[str, int, int]]


def create_app(settings: Settings, llm: LLM | None = None) -> FastAPI:
    app = FastAPI(title="Lesson Generator")
    bundle = load_bundle(settings.skill_dir)
    fonts_css = default_fonts_css()
    app.state.llm = llm or OpenRouterLLM(
        api_key=settings.openrouter_api_key,
        model=settings.model,
        reasoning_effort=settings.reasoning_effort,
        max_output_tokens=settings.max_output_tokens,
    )

    def _job_or_404(job_id: str) -> Job:
        try:
            return load_job(settings.jobs_dir, job_id)
        except (ValueError, FileNotFoundError):
            raise HTTPException(status_code=404, detail="unknown job") from None

    def _unit_or_404(job_id: str, unit_id: str):
        job = _job_or_404(job_id)
        for unit in job.units:
            if unit.unit_id == unit_id:
                return job, unit
        raise HTTPException(status_code=404, detail="unknown unit")

    @app.post("/api/jobs")
    def create_job(body: CreateJobBody) -> dict:
        pdf = Path(body.pdf)
        if not pdf.is_file():
            raise HTTPException(status_code=400, detail=f"no such PDF: {pdf}")
        try:
            job = new_job(settings.jobs_dir, pdf, page_count(pdf), body.ranges)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        return job.to_dict()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        return _job_or_404(job_id).to_dict()

    @app.post("/api/jobs/{job_id}/units/{unit_id}/generate")
    def generate(job_id: str, unit_id: str) -> dict:
        job, unit = _unit_or_404(job_id, unit_id)
        artifact = settings.jobs_dir / job.job_id / "units" / unit.unit_id / "artifact.html"
        if unit.status is UnitStatus.OK and artifact.is_file():
            return {
                "status": "ok",
                "calls": 0,
                "requested_refs": [],
                "findings": [],
                "artifact_url": f"/api/jobs/{job.job_id}/units/{unit.unit_id}/artifact.html",
            }
        set_unit_status(settings.jobs_dir, job.job_id, unit.unit_id, UnitStatus.RUNNING)
        try:
            pages = rasterize(
                Path(job.source_pdf),
                unit.page_numbers,
                settings.jobs_dir / job.job_id / "pages",
            )
        except (ValueError, RuntimeError) as error:
            set_unit_status(settings.jobs_dir, job.job_id, unit.unit_id, UnitStatus.FAILED)
            raise HTTPException(status_code=400, detail=str(error)) from None

        result = generate_unit(
            UnitRequest(unit.unit_id, unit.label, unit.page_numbers, pages),
            llm=app.state.llm,
            bundle=bundle,
            fonts_css=fonts_css,
            out_dir=settings.jobs_dir / job.job_id,
            status_callback=lambda status: set_unit_status(
                settings.jobs_dir, job.job_id, unit.unit_id, status
            ),
        )
        set_unit_status(settings.jobs_dir, job.job_id, unit.unit_id, result.status)
        artifact_url = (
            f"/api/jobs/{job.job_id}/units/{unit.unit_id}/artifact.html"
            if result.artifact_path is not None
            else None
        )
        return {
            "status": result.status.value,
            "calls": result.calls,
            "requested_refs": result.requested_refs,
            "findings": result.findings,
            "artifact_url": artifact_url,
        }

    @app.get("/api/jobs/{job_id}/units/{unit_id}/artifact.html")
    def artifact(job_id: str, unit_id: str, download: int = 0) -> FileResponse:
        job, unit = _unit_or_404(job_id, unit_id)
        path = settings.jobs_dir / job.job_id / "units" / unit.unit_id / "artifact.html"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="artifact not generated yet")
        disposition = "attachment" if download else "inline"
        return FileResponse(
            path,
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'{disposition}; filename="artifact.html"'},
        )

    return app
