from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.generate.unit import UnitResult, UnitStatus
from backend.settings import Settings


class RecordingLLM:
    def __init__(self, template: str) -> None:
        self._template = template
        self.calls = 0

    def complete(self, messages, tools=None):
        from backend.llm.client import LLMReply

        self.calls += 1
        return LLMReply(text=self._template)


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    pdf = tmp_path / "source.pdf"
    doc = pymupdf.open()
    for number in range(6):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 120), f"Page {number + 1}")
    doc.save(pdf)
    doc.close()
    settings = Settings(
        openrouter_api_key="sk-test",
        model="deepseek/deepseek-v4.1-flash-20260910",
        reasoning_effort="low",
        max_output_tokens=32768,
        skill_dir=Path("diagram-design"),
        jobs_dir=tmp_path / "jobs",
    )
    template = (Path("diagram-design") / "assets" / "template.html").read_text(encoding="utf-8")
    app = create_app(settings, llm=RecordingLLM(template))
    app.state.source_pdf = pdf
    return TestClient(app)


def test_create_job_returns_units_with_page_ranges(client: TestClient) -> None:
    response = client.post(
        "/api/jobs",
        json={"pdf": str(client.app.state.source_pdf), "ranges": [["Unit A", 1, 3], ["Unit B", 4, 6]]},
    )

    assert response.status_code == 200
    job = response.json()
    assert job["page_count"] == 6
    assert [unit["label"] for unit in job["units"]] == ["Unit A", "Unit B"]


def test_generate_writes_artifact_that_both_routes_serve_identically(client: TestClient) -> None:
    job = client.post(
        "/api/jobs",
        json={"pdf": str(client.app.state.source_pdf), "ranges": [["Unit A", 1, 2]]},
    ).json()
    unit_id = job["units"][0]["unit_id"]

    generated = client.post(f"/api/jobs/{job['job_id']}/units/{unit_id}/generate")

    assert generated.status_code == 200
    assert generated.json()["status"] == "ok"
    assert generated.json()["artifact_url"] == (
        f"/api/jobs/{job['job_id']}/units/{unit_id}/artifact.html"
    )

    inline = client.get(f"/api/jobs/{job['job_id']}/units/{unit_id}/artifact.html")
    download = client.get(
        f"/api/jobs/{job['job_id']}/units/{unit_id}/artifact.html?download=1"
    )

    assert inline.status_code == 200
    assert inline.content == download.content
    assert "attachment" in download.headers["content-disposition"]
    assert inline.headers["content-disposition"].startswith("inline")


@pytest.mark.parametrize("status", [UnitStatus.FAILED, UnitStatus.NEEDS_ATTENTION])
def test_generation_without_published_artifact_returns_null_artifact_url(
    client: TestClient, monkeypatch, status: UnitStatus
) -> None:
    job = client.post(
        "/api/jobs",
        json={"pdf": str(client.app.state.source_pdf), "ranges": [["Unit A", 1, 2]]},
    ).json()
    unit_id = job["units"][0]["unit_id"]

    def generate_without_artifact(*args, **kwargs) -> UnitResult:
        return UnitResult(
            status=status,
            calls=1,
            artifact_path=None,
            findings=["no artifact was published"],
        )

    monkeypatch.setattr("backend.api.app.generate_unit", generate_without_artifact)

    response = client.post(f"/api/jobs/{job['job_id']}/units/{unit_id}/generate")

    assert response.status_code == 200
    assert response.json()["status"] == status.value
    assert response.json()["artifact_url"] is None
    assert client.get(f"/api/jobs/{job['job_id']}/units/{unit_id}/artifact.html").status_code == 404


def test_generate_is_refused_for_a_status_that_is_not_pending(client: TestClient) -> None:
    job = client.post(
        "/api/jobs",
        json={"pdf": str(client.app.state.source_pdf), "ranges": [["Unit A", 1, 2]]},
    ).json()
    unit_id = job["units"][0]["unit_id"]
    client.post(f"/api/jobs/{job['job_id']}/units/{unit_id}/generate")

    again = client.post(f"/api/jobs/{job['job_id']}/units/{unit_id}/generate")

    assert again.json()["status"] == "ok"
    assert client.app.state.llm.calls == 1  # re-running an ok unit is a no-op


def test_artifact_route_validates_the_job_id(client: TestClient) -> None:
    response = client.get("/api/jobs/..%2F..%2Fetc/units/x/artifact.html")

    assert response.status_code in {400, 404}
