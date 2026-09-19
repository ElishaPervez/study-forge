import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from threading import Event, Lock

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import backend.api.app as app_module
import backend.jobs.store as store_module
from backend.api.app import create_app
from backend.generate.revision import revise_guide as real_revise_guide
from backend.jobs.store import load_guide, save_guide
from backend.llm.client import LLMError, LLMReply
from backend.settings import Settings
from backend.verify.self_check import CheckResult

SKILL_DIR = Path("diagram-design")
GOOD = (SKILL_DIR / "assets" / "template.html").read_text(encoding="utf-8")


class QueueLLM:
    def __init__(self, replies: list[object]) -> None:
        self.replies = list(replies)
        self.seen: list[list[dict]] = []

    def complete(self, messages, tools=None) -> LLMReply:
        self.seen.append(list(messages))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


class ConcurrentLLM:
    def __init__(self, invalid_html: str) -> None:
        self.calls = 0
        self.first_started = Event()
        self.second_started = Event()
        self.release_first = Event()
        self._calls_lock = Lock()
        self.invalid_html = invalid_html

    def complete(self, messages, tools=None) -> LLMReply:
        with self._calls_lock:
            self.calls += 1
            call_number = self.calls
        if call_number == 1:
            self.first_started.set()
            if not self.release_first.wait(timeout=5):
                raise RuntimeError("test did not release the first generation")
            return LLMReply(text=GOOD)
        self.second_started.set()
        return LLMReply(text=self.invalid_html)


class ConcurrentRevisionLLM:
    def __init__(self) -> None:
        self.calls = 0
        self.first_started = Event()
        self.second_started = Event()
        self.release_first = Event()
        self._calls_lock = Lock()

    def complete(self, messages, tools=None) -> LLMReply:
        with self._calls_lock:
            self.calls += 1
            call_number = self.calls
        revision_text = messages[1]["content"][-1]["text"]
        current_html = revision_text.split(
            "Current complete HTML document:\n<current-html>\n", 1
        )[1].split("\n</current-html>", 1)[0]
        if call_number == 1:
            self.first_started.set()
            if not self.release_first.wait(timeout=5):
                raise RuntimeError("test did not release the first revision")
            marker = "First revision"
        else:
            self.second_started.set()
            marker = "Second revision"
        return LLMReply(
            text=current_html.replace("</body>", f"<p>{marker}</p></body>")
        )


def _client(tmp_path: Path, replies: list[object]) -> TestClient:
    pdf = tmp_path / "course.pdf"
    document = pymupdf.open()
    for number in range(3):
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 120), f"Page {number + 1}")
    document.save(pdf)
    document.close()
    settings = Settings(
        openrouter_api_key="sk-test",
        model="deepseek/deepseek-v4.1-flash",
        reasoning_effort="low",
        max_output_tokens=32768,
        skill_dir=SKILL_DIR,
        jobs_dir=tmp_path / "jobs",
    )
    app = create_app(settings, llm=QueueLLM(replies))
    app.state.source_pdf = pdf
    return TestClient(app)


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    pdf = tmp_path / "course.pdf"
    document = pymupdf.open()
    for number in range(3):
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 120), f"Page {number + 1}")
    document.save(pdf)
    document.close()
    settings = Settings(
        openrouter_api_key="sk-test",
        model="deepseek/deepseek-v4.1-flash",
        reasoning_effort="low",
        max_output_tokens=32768,
        skill_dir=SKILL_DIR,
        jobs_dir=tmp_path / "jobs",
    )
    app = create_app(settings, llm=QueueLLM([]))
    app.state.source_pdf = pdf
    return TestClient(app)


def _source(client: TestClient) -> dict:
    return client.post(
        "/api/sources", json={"paths": [str(client.app.state.source_pdf)]}
    ).json()


def _image_bytes(format: str, color: tuple[int, int, int]) -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), color).save(output, format=format)
    return output.getvalue()


def test_source_preview_routes_serve_pdf_pages_and_ordered_original_images(
    client: TestClient, tmp_path: Path
) -> None:
    pdf_source = _source(client)

    pdf_preview = client.get(f"/api/sources/{pdf_source['source_id']}/pages/2")

    assert pdf_preview.status_code == 200
    assert pdf_preview.headers["content-type"].startswith("image/jpeg")
    assert pdf_preview.content.startswith(b"\xff\xd8")

    first = tmp_path / "first.png"
    second = tmp_path / "second.webp"
    first_bytes = _image_bytes("PNG", (25, 75, 125))
    second_bytes = _image_bytes("WEBP", (50, 100, 150))
    first.write_bytes(first_bytes)
    second.write_bytes(second_bytes)
    image_source = client.post(
        "/api/sources", json={"paths": [str(first), str(second)]}
    ).json()

    first_preview = client.get(f"/api/sources/{image_source['source_id']}/images/1")
    second_preview = client.get(f"/api/sources/{image_source['source_id']}/images/2")

    assert first_preview.status_code == 200
    assert first_preview.headers["content-type"].startswith("image/png")
    assert first_preview.content == first_bytes
    assert second_preview.status_code == 200
    assert second_preview.headers["content-type"].startswith("image/webp")
    assert second_preview.content == second_bytes
    assert client.get(f"/api/sources/{image_source['source_id']}/images/0").status_code == 404
    assert client.get(f"/api/sources/{image_source['source_id']}/pages/1").status_code == 400


@pytest.mark.parametrize("page_number", [0, 4])
def test_pdf_preview_returns_404_for_an_out_of_range_page(
    client: TestClient, page_number: int
) -> None:
    source = _source(client)

    response = client.get(f"/api/sources/{source['source_id']}/pages/{page_number}")

    assert response.status_code == 404


def test_pdf_preview_returns_404_when_the_stored_pdf_is_missing(client: TestClient) -> None:
    source = _source(client)
    stored_pdf = client.app.state.settings.jobs_dir / "sources" / source["source_id"] / source["files"][0]
    stored_pdf.unlink()

    response = client.get(f"/api/sources/{source['source_id']}/pages/1")

    assert response.status_code == 404


def test_pdf_preview_returns_404_when_the_stored_pdf_is_corrupt(client: TestClient) -> None:
    source = _source(client)
    stored_pdf = client.app.state.settings.jobs_dir / "sources" / source["source_id"] / source["files"][0]
    stored_pdf.write_bytes(b"not a PDF")

    response = client.get(f"/api/sources/{source['source_id']}/pages/1")

    assert response.status_code == 404
    assert "choose the source again" in response.json()["detail"].lower()
    assert "failed to open file" not in response.json()["detail"].lower()


def test_corrupt_stored_pdf_generation_points_back_to_source(client: TestClient) -> None:
    source = _source(client)
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    stored_pdf = client.app.state.settings.jobs_dir / "sources" / source["source_id"] / source["files"][0]
    stored_pdf.write_bytes(b"not a PDF")

    failed = client.post(f"/api/guides/{guide['guide_id']}/generate")

    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    assert "choose the source again" in failed.json()["error"].lower()
    assert "failed to open file" not in failed.json()["error"].lower()
    assert any("failed to open file" in finding.lower() for finding in failed.json()["findings"])


def test_corrupt_stored_pdf_revision_points_back_to_source(client: TestClient) -> None:
    source = _source(client)
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    guide_record = load_guide(client.app.state.settings.jobs_dir, guide["guide_id"])
    guide_dir = client.app.state.settings.jobs_dir / "guides" / guide["guide_id"]
    (guide_dir / "artifact.html").write_text(GOOD, encoding="utf-8")
    save_guide(client.app.state.settings.jobs_dir, replace(guide_record, status="ok"))
    stored_pdf = client.app.state.settings.jobs_dir / "sources" / source["source_id"] / source["files"][0]
    stored_pdf.write_bytes(b"not a PDF")

    failed = client.post(
        f"/api/guides/{guide['guide_id']}/revisions",
        json={"selected_text": "text", "instruction": "change", "mode": "custom"},
    )

    assert failed.status_code == 400
    assert "choose the source again" in failed.json()["detail"].lower()
    assert "failed to open file" not in failed.json()["detail"].lower()


def test_duplicate_forge_requests_create_two_history_entries(client: TestClient) -> None:
    source = _source(client)
    body = {"source_id": source["source_id"], "selection": {"mode": "all"}}

    first = client.post("/api/guides", json=body).json()
    second = client.post("/api/guides", json=body).json()

    assert first["guide_id"] != second["guide_id"]
    listed = client.get("/api/guides")
    assert listed.status_code == 200
    assert len(listed.json()) == 2
    assert listed.json()[0]["source"]["source_id"] == source["source_id"]


def test_history_skips_a_guide_with_invalid_persisted_identifiers(
    client: TestClient,
) -> None:
    source = _source(client)
    valid = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    malformed_id = "c" * 32
    malformed_dir = client.app.state.settings.jobs_dir / "guides" / malformed_id
    malformed_dir.mkdir(parents=True)
    malformed = dict(valid)
    malformed["guide_id"] = "../escape"
    malformed["source_id"] = "../outside"
    malformed.pop("source")
    malformed_dir.joinpath("guide.json").write_text(
        json.dumps(malformed), encoding="utf-8"
    )

    history = client.get("/api/guides")

    assert history.status_code == 200
    assert [entry["guide_id"] for entry in history.json()] == [valid["guide_id"]]


def test_old_multi_range_job_is_a_clear_non_actionable_history_notice(client: TestClient) -> None:
    legacy_id = "b" * 32
    legacy_dir = client.app.state.settings.jobs_dir / legacy_id
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "job.json").write_text(
        json.dumps(
            {
                "job_id": legacy_id,
                "source_pdf": "C:/old/course.pdf",
                "page_count": 8,
                "units": [
                    {
                        "unit_id": "unit-01",
                        "label": "Part one",
                        "page_start": 1,
                        "page_end": 4,
                        "status": "pending",
                    },
                    {
                        "unit_id": "unit-02",
                        "label": "Part two",
                        "page_start": 5,
                        "page_end": 8,
                        "status": "pending",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    response = client.get("/api/guides")

    assert response.status_code == 200
    assert response.json() == [
        {
            "kind": "legacy",
            "history_id": f"legacy-{legacy_id}",
            "job_id": legacy_id,
            "name": "course.pdf",
            "status": "legacy",
            "message": (
                "This older multi-range job is not compatible with Study Forge v1. "
                "Choose the source again to create a new guide."
            ),
            "created_at": response.json()[0]["created_at"],
            "updated_at": response.json()[0]["updated_at"],
        }
    ]


def test_missing_stored_source_keeps_history_with_a_source_recovery_message(
    client: TestClient,
) -> None:
    source = _source(client)
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    shutil.rmtree(client.app.state.settings.jobs_dir / "sources" / source["source_id"])

    listed = client.get("/api/guides")
    detail = client.get(f"/api/guides/{guide['guide_id']}")

    assert listed.status_code == 200
    assert listed.json()[0]["guide_id"] == guide["guide_id"]
    assert listed.json()[0]["source"] is None
    assert "choose the source again" in listed.json()[0]["source_error"].lower()
    assert detail.status_code == 200
    assert detail.json()["source"] is None


def test_reregistering_the_original_source_repairs_a_missing_stored_file(
    client: TestClient,
) -> None:
    source = _source(client)
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    stored_pdf = client.app.state.settings.jobs_dir / "sources" / source["source_id"] / source["files"][0]
    original_pdf = client.app.state.source_pdf
    stored_pdf.unlink()

    repaired = client.post("/api/sources", json={"paths": [str(original_pdf)]})

    assert repaired.status_code == 200
    assert repaired.json()["source_id"] == source["source_id"]
    assert stored_pdf.is_file()
    assert stored_pdf.read_bytes() == original_pdf.read_bytes()
    reopened = client.get(f"/api/guides/{guide['guide_id']}")
    assert reopened.status_code == 200
    assert reopened.json()["source"]["file_details"][0]["exists"] is True


def test_reregistering_the_original_source_repairs_a_corrupt_stored_pdf(
    client: TestClient,
) -> None:
    source = _source(client)
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    stored_pdf = client.app.state.settings.jobs_dir / "sources" / source["source_id"] / source["files"][0]
    original_bytes = client.app.state.source_pdf.read_bytes()
    stored_pdf.write_bytes(b"not a PDF")

    repaired = client.post(
        "/api/sources", json={"paths": [str(client.app.state.source_pdf)]}
    )
    reopened = client.get(f"/api/guides/{guide['guide_id']}")
    preview = client.get(f"/api/sources/{source['source_id']}/pages/1")

    assert repaired.status_code == 200
    assert repaired.json()["source_id"] == source["source_id"]
    assert stored_pdf.read_bytes() == original_bytes
    assert reopened.status_code == 200
    assert reopened.json()["source"] is not None
    assert preview.status_code == 200


@pytest.mark.parametrize("manifest_name", ["source.json", "manifest.json"])
def test_reregistering_the_original_source_repairs_a_damaged_manifest_and_keeps_guide(
    client: TestClient, manifest_name: str
) -> None:
    source = _source(client)
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    source_dir = client.app.state.settings.jobs_dir / "sources" / source["source_id"]
    source_manifest = source_dir / "source.json"
    if manifest_name == "manifest.json":
        source_manifest.rename(source_dir / manifest_name)
    (source_dir / manifest_name).write_text("{not valid json", encoding="utf-8")

    unavailable = client.get(f"/api/guides/{guide['guide_id']}")
    repaired = client.post(
        "/api/sources", json={"paths": [str(client.app.state.source_pdf)]}
    )
    reopened = client.get(f"/api/guides/{guide['guide_id']}")
    preview = client.get(f"/api/sources/{source['source_id']}/pages/1")

    assert unavailable.status_code == 200
    assert unavailable.json()["source"] is None
    assert "choose the source again" in unavailable.json()["source_error"].lower()
    assert repaired.status_code == 200
    assert repaired.json()["source_id"] == source["source_id"]
    assert (source_dir / "source.json").is_file()
    assert reopened.status_code == 200
    assert reopened.json()["source"] is not None
    assert preview.status_code == 200


def test_reregistering_a_structurally_inconsistent_source_keeps_guide_linkage(
    client: TestClient,
) -> None:
    source = _source(client)
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    source_dir = client.app.state.settings.jobs_dir / "sources" / source["source_id"]
    manifest_path = source_dir / "source.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = []
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    repaired = client.post(
        "/api/sources", json={"paths": [str(client.app.state.source_pdf)]}
    )
    reopened = client.get(f"/api/guides/{guide['guide_id']}")

    assert repaired.status_code == 200
    assert repaired.json()["source_id"] == source["source_id"]
    assert reopened.status_code == 200
    assert reopened.json()["source_id"] == guide["source_id"]
    assert reopened.json()["source"] is not None


@pytest.mark.parametrize("damage", ["missing", "corrupt", "manifest"])
def test_unreferenced_unavailable_source_can_be_removed(
    client: TestClient, damage: str
) -> None:
    source = _source(client)
    source_dir = client.app.state.settings.jobs_dir / "sources" / source["source_id"]
    stored_pdf = source_dir / source["files"][0]
    if damage == "missing":
        stored_pdf.unlink()
    elif damage == "corrupt":
        stored_pdf.write_bytes(b"not a PDF")
    else:
        (source_dir / "source.json").write_text("{not valid json", encoding="utf-8")

    removed = client.delete(f"/api/sources/{source['source_id']}")

    assert removed.status_code == 200
    assert removed.json() == {
        "source_id": source["source_id"],
        "deleted": True,
        "retained": False,
    }
    assert not source_dir.exists()


@pytest.mark.parametrize("damage", ["missing", "corrupt", "manifest"])
def test_referenced_unavailable_source_is_retained_on_removal(
    client: TestClient, damage: str
) -> None:
    source = _source(client)
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    source_dir = client.app.state.settings.jobs_dir / "sources" / source["source_id"]
    stored_pdf = source_dir / source["files"][0]
    if damage == "missing":
        stored_pdf.unlink()
    elif damage == "corrupt":
        stored_pdf.write_bytes(b"not a PDF")
    else:
        (source_dir / "source.json").write_text("{not valid json", encoding="utf-8")

    removed = client.delete(f"/api/sources/{source['source_id']}")

    assert removed.status_code == 200
    assert removed.json()["source_id"] == source["source_id"]
    assert removed.json()["deleted"] is False
    assert removed.json()["retained"] is True
    assert source_dir.exists()
    assert client.get(f"/api/guides/{guide['guide_id']}").status_code == 200


def test_corrupt_stored_sources_are_unavailable_on_normal_reads(
    client: TestClient,
) -> None:
    source = _source(client)
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    stored_pdf = client.app.state.settings.jobs_dir / "sources" / source["source_id"] / source["files"][0]
    stored_pdf.write_bytes(b"not a PDF")

    listed = client.get("/api/guides")
    detail = client.get(f"/api/guides/{guide['guide_id']}")

    assert listed.status_code == 200
    assert listed.json()[0]["source"] is None
    assert "choose the source again" in listed.json()[0]["source_error"].lower()
    assert detail.status_code == 200
    assert detail.json()["source"] is None
    assert "choose the source again" in detail.json()["source_error"].lower()


def test_corrupt_stored_image_is_unavailable_on_normal_reads(
    client: TestClient,
) -> None:
    original_image = client.app.state.settings.jobs_dir.parent / "healthy.png"
    original_image.write_bytes(_image_bytes("PNG", (25, 75, 125)))
    source = client.post("/api/sources", json={"paths": [str(original_image)]}).json()
    client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "images"}},
    ).json()
    stored_image = client.app.state.settings.jobs_dir / "sources" / source["source_id"] / source["files"][0]
    stored_image.write_bytes(b"not an image")

    listed = client.get("/api/guides")
    preview = client.get(f"/api/sources/{source['source_id']}/images/1")

    assert listed.status_code == 200
    assert listed.json()[0]["source"] is None
    assert "choose the source again" in listed.json()[0]["source_error"].lower()
    assert preview.status_code == 404
    assert "choose the source again" in preview.json()["detail"].lower()


def test_source_removal_cannot_race_guide_creation_into_inconsistency(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(client)
    removal_check_started = Event()
    allow_removal_check = Event()
    creation_started = Event()
    real_list_guides = app_module.list_guides
    real_new_guide = app_module.new_guide

    def blocking_list_guides(jobs_dir):
        entries = real_list_guides(jobs_dir)
        if not removal_check_started.is_set():
            removal_check_started.set()
            if not allow_removal_check.wait(timeout=5):
                raise RuntimeError("test did not release source removal")
        return entries

    def track_new_guide(*args, **kwargs):
        creation_started.set()
        return real_new_guide(*args, **kwargs)

    monkeypatch.setattr(app_module, "list_guides", blocking_list_guides)
    monkeypatch.setattr(app_module, "new_guide", track_new_guide)
    first_client = TestClient(client.app)
    second_client = TestClient(client.app)

    with ThreadPoolExecutor(max_workers=2) as executor:
        removal = executor.submit(first_client.delete, f"/api/sources/{source['source_id']}")
        assert removal_check_started.wait(timeout=2)
        creation = executor.submit(
            second_client.post,
            "/api/guides",
            json={"source_id": source["source_id"], "selection": {"mode": "all"}},
        )
        if creation_started.wait(timeout=0.5):
            allow_removal_check.set()
            removal_response = removal.result(timeout=5)
            creation_response = creation.result(timeout=5)
            pytest.fail(
                "guide creation committed while source removal still held a stale reference check: "
                f"remove={removal_response.status_code}, create={creation_response.status_code}"
            )
        allow_removal_check.set()
        removal_response = removal.result(timeout=5)
        creation_response = creation.result(timeout=5)

    assert removal_response.status_code == 200
    if creation_response.status_code == 200:
        created = creation_response.json()
        assert client.get(f"/api/sources/{source['source_id']}").status_code == 200
        assert client.get(f"/api/guides/{created['guide_id']}").status_code == 200
    else:
        assert creation_response.status_code == 404
        assert client.get(f"/api/sources/{source['source_id']}").status_code == 404


def test_last_guide_deletion_cannot_race_guide_creation_into_inconsistency(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(client)
    existing = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    deletion_check_started = Event()
    allow_deletion_check = Event()
    creation_started = Event()
    guide_dir = client.app.state.settings.jobs_dir / "guides" / existing["guide_id"]
    real_list_guides = store_module.list_guides
    real_new_guide = app_module.new_guide

    def blocking_list_guides(jobs_dir):
        entries = real_list_guides(jobs_dir)
        if not guide_dir.exists() and not deletion_check_started.is_set():
            deletion_check_started.set()
            if not allow_deletion_check.wait(timeout=5):
                raise RuntimeError("test did not release guide deletion")
        return entries

    def track_new_guide(*args, **kwargs):
        creation_started.set()
        return real_new_guide(*args, **kwargs)

    monkeypatch.setattr(store_module, "list_guides", blocking_list_guides)
    monkeypatch.setattr(app_module, "new_guide", track_new_guide)
    first_client = TestClient(client.app)
    second_client = TestClient(client.app)

    with ThreadPoolExecutor(max_workers=2) as executor:
        deletion = executor.submit(first_client.delete, f"/api/guides/{existing['guide_id']}")
        assert deletion_check_started.wait(timeout=2)
        creation = executor.submit(
            second_client.post,
            "/api/guides",
            json={"source_id": source["source_id"], "selection": {"mode": "all"}},
        )
        if creation_started.wait(timeout=0.5):
            allow_deletion_check.set()
            deletion_response = deletion.result(timeout=5)
            creation_response = creation.result(timeout=5)
            pytest.fail(
                "guide creation committed while last-guide deletion still held a stale reference check: "
                f"delete={deletion_response.status_code}, create={creation_response.status_code}"
            )
        allow_deletion_check.set()
        deletion_response = deletion.result(timeout=5)
        creation_response = creation.result(timeout=5)

    assert deletion_response.status_code == 200
    if creation_response.status_code == 200:
        created = creation_response.json()
        assert client.get(f"/api/sources/{source['source_id']}").status_code == 200
        assert client.get(f"/api/guides/{created['guide_id']}").status_code == 200
    else:
        assert creation_response.status_code == 404
        assert client.get(f"/api/sources/{source['source_id']}").status_code == 404


def test_legacy_manifest_can_be_registered_reopened_and_used_for_a_new_guide(
    client: TestClient,
) -> None:
    source = _source(client)
    source_dir = client.app.state.settings.jobs_dir / "sources" / source["source_id"]
    (source_dir / "source.json").rename(source_dir / "manifest.json")

    registered = client.post(
        "/api/sources", json={"paths": [str(client.app.state.source_pdf)]}
    )
    reopened_client = TestClient(create_app(client.app.state.settings, llm=QueueLLM([])))
    reopened = reopened_client.get(f"/api/sources/{source['source_id']}")
    guide = reopened_client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    )

    assert registered.status_code == 200
    assert registered.json()["source_id"] == source["source_id"]
    assert reopened.status_code == 200
    assert reopened.json()["kind"] == "pdf"
    assert guide.status_code == 200
    assert guide.json()["source_id"] == source["source_id"]


@pytest.mark.parametrize(
    "unsafe_name",
    ["/outside.pdf", "../outside.pdf", r"..\outside.pdf", r"C:\outside.pdf"],
)
def test_unsafe_manifest_filenames_make_pdf_source_unavailable(
    client: TestClient, unsafe_name: str
) -> None:
    source = _source(client)
    manifest_path = (
        client.app.state.settings.jobs_dir
        / "sources"
        / source["source_id"]
        / "source.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = [unsafe_name]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    listed = client.get(f"/api/sources/{source['source_id']}")
    preview = client.get(f"/api/sources/{source['source_id']}/pages/1")
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    )

    assert listed.status_code == 404
    assert preview.status_code == 404
    assert guide.status_code == 404


def test_unsafe_legacy_manifest_filename_cannot_serve_an_image_outside_its_directory(
    client: TestClient,
) -> None:
    image = client.app.state.settings.jobs_dir.parent / "outside.png"
    image.write_bytes(b"must not be served")
    registered = client.post("/api/sources", json={"paths": [str(image)]}).json()
    source_dir = client.app.state.settings.jobs_dir / "sources" / registered["source_id"]
    (source_dir / "source.json").rename(source_dir / "manifest.json")
    manifest_path = source_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = [r"..\outside.png"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    listed = client.get(f"/api/sources/{registered['source_id']}")
    served = client.get(f"/api/sources/{registered['source_id']}/images/1")

    assert listed.status_code == 404
    assert served.status_code == 404


@pytest.mark.parametrize("malformed_manifest", [[], {"kind": "pdf", "files": []}])
def test_malformed_source_manifest_is_a_safe_not_found(
    client: TestClient, malformed_manifest: object
) -> None:
    source = _source(client)
    manifest_path = (
        client.app.state.settings.jobs_dir
        / "sources"
        / source["source_id"]
        / "source.json"
    )
    manifest_path.write_text(json.dumps(malformed_manifest), encoding="utf-8")

    response = client.get(f"/api/sources/{source['source_id']}")

    assert response.status_code == 404


def test_pdf_page_count_mismatch_is_unavailable_until_reregistered(
    client: TestClient,
) -> None:
    source = _source(client)
    manifest_path = (
        client.app.state.settings.jobs_dir
        / "sources"
        / source["source_id"]
        / "source.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["page_count"] = source["page_count"] - 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    unavailable = client.get(f"/api/sources/{source['source_id']}")
    repaired = client.post(
        "/api/sources", json={"paths": [str(client.app.state.source_pdf)]}
    )
    reopened = client.get(f"/api/sources/{source['source_id']}")

    assert unavailable.status_code == 404
    assert repaired.status_code == 200
    assert repaired.json()["page_count"] == source["page_count"]
    assert reopened.status_code == 200
    assert reopened.json()["page_count"] == source["page_count"]
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted["page_count"] == source["page_count"]


def test_generation_failure_from_a_missing_stored_file_points_back_to_source(
    client: TestClient,
) -> None:
    source = _source(client)
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    stored_pdf = client.app.state.settings.jobs_dir / "sources" / source["source_id"] / source["files"][0]
    stored_pdf.unlink()

    failed = client.post(f"/api/guides/{guide['guide_id']}/generate")

    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    assert "choose the source again" in failed.json()["error"].lower()


def test_generation_exception_preserves_a_user_renamed_guide(client: TestClient) -> None:
    source = _source(client)
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    renamed = client.patch(
        f"/api/guides/{guide['guide_id']}", json={"name": "Saved Before Failure"}
    )
    shutil.rmtree(client.app.state.settings.jobs_dir / "sources" / source["source_id"])

    failed = client.post(f"/api/guides/{guide['guide_id']}/generate")

    assert renamed.json()["name"] == "Saved Before Failure"
    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    assert failed.json()["name"] == "Saved Before Failure"


def test_revision_of_a_guide_with_a_missing_source_points_back_to_source(
    client: TestClient,
) -> None:
    source = _source(client)
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    guide_dir = client.app.state.settings.jobs_dir / "guides" / guide["guide_id"]
    (guide_dir / "artifact.html").write_text("<html>saved</html>", encoding="utf-8")
    (guide_dir / "guide.json").write_text(
        json.dumps({**guide, "status": "ok", "artifact_url": None}),
        encoding="utf-8",
    )
    shutil.rmtree(client.app.state.settings.jobs_dir / "sources" / source["source_id"])

    response = client.post(
        f"/api/guides/{guide['guide_id']}/revisions",
        json={
            "selected_text": "saved",
            "instruction": "Clarify this.",
            "mode": "custom",
        },
    )

    assert response.status_code == 400
    assert "choose the source again" in response.json()["detail"].lower()


@pytest.mark.parametrize("interrupted_status", ["pending", "running", "verifying", "repairing"])
def test_restart_recovers_interrupted_guides_without_duplicate_or_artifact_loss(
    client: TestClient,
    interrupted_status: str,
) -> None:
    source = _source(client)
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    guide_record = load_guide(client.app.state.settings.jobs_dir, guide["guide_id"])
    save_guide(
        client.app.state.settings.jobs_dir,
        replace(guide_record, status=interrupted_status),
    )
    artifact_path = client.app.state.settings.jobs_dir / "guides" / guide["guide_id"] / "artifact.html"
    artifact_bytes = b"<html><body>saved before interruption</body></html>"
    artifact_path.write_bytes(artifact_bytes)
    metadata_path = artifact_path.with_name("guide.json")
    os.utime(metadata_path, (1, 1))

    restarted = TestClient(
        create_app(
            client.app.state.settings,
            llm=QueueLLM([LLMError("retry unavailable", retryable=False)]),
        )
    )
    history = restarted.get("/api/guides")

    assert history.status_code == 200
    assert history.json()[0]["guide_id"] == guide["guide_id"]
    assert history.json()[0]["status"] == "failed"
    assert "interrupted" in history.json()[0]["error"].lower()
    assert len(history.json()) == 1
    assert artifact_path.read_bytes() == artifact_bytes

    retried = restarted.post(f"/api/guides/{guide['guide_id']}/retry")

    assert retried.status_code == 200
    assert retried.json()["guide_id"] == guide["guide_id"]
    assert len(restarted.get("/api/guides").json()) == 1


def test_restart_loads_persisted_failed_entries(client: TestClient) -> None:
    source = _source(client)
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    client.app.state.llm.replies.append(LLMError("model unavailable", retryable=False))
    failed = client.post(f"/api/guides/{guide['guide_id']}/generate")
    assert failed.status_code == 200

    restarted = TestClient(create_app(client.app.state.settings, llm=QueueLLM([])))
    history = restarted.get("/api/guides")

    assert history.status_code == 200
    assert history.json()[0]["guide_id"] == guide["guide_id"]
    assert history.json()[0]["status"] == "failed"


def test_guide_creation_validates_pdf_selection_and_image_selection(client: TestClient) -> None:
    source = _source(client)
    invalid = client.post(
        "/api/guides",
        json={
            "source_id": source["source_id"],
            "selection": {"mode": "custom", "start": 0, "end": 2},
        },
    )
    assert invalid.status_code == 400


def test_failed_guide_is_listed_and_retry_reuses_the_same_id(tmp_path: Path) -> None:
    failing = LLMError("model unavailable", retryable=False)
    client = _client(tmp_path, [failing, LLMReply(text=GOOD)])
    pdf = tmp_path / "course.pdf"
    client.app.state.source_pdf = pdf
    source = client.post("/api/sources", json={"paths": [str(pdf)]}).json()
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()

    failed = client.post(f"/api/guides/{guide['guide_id']}/generate")
    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    assert client.get("/api/guides").json()[0]["status"] == "failed"

    retried = client.post(f"/api/guides/{guide['guide_id']}/retry")

    assert retried.status_code == 200
    assert retried.json()["guide_id"] == guide["guide_id"]
    assert retried.json()["status"] == "ok"
    assert retried.json()["error"] is None


def test_needs_attention_generation_keeps_previous_artifact_unpublished(tmp_path: Path) -> None:
    invalid_html = "<html><head><title>Rejected</title></head><body>invalid</body></html>"
    client = _client(tmp_path, [LLMReply(text=invalid_html), LLMReply(text=invalid_html)])
    pdf = tmp_path / "course.pdf"
    client.app.state.source_pdf = pdf
    source = client.post("/api/sources", json={"paths": [str(pdf)]}).json()
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    final_path = client.app.state.settings.jobs_dir / "guides" / guide["guide_id"] / "artifact.html"
    previous_artifact = b"<html><body>verified artifact</body></html>"
    final_path.write_bytes(previous_artifact)

    failed = client.post(f"/api/guides/{guide['guide_id']}/generate")

    assert failed.status_code == 200
    assert failed.json()["status"] == "needs-attention"
    assert final_path.read_bytes() == previous_artifact
    assert client.get(f"/api/guides/{guide['guide_id']}/artifact.html").status_code == 409


def test_concurrent_generation_requests_share_one_serialized_attempt(
    client: TestClient,
) -> None:
    invalid_html = "<html><head><title>Rejected</title></head><body>invalid</body></html>"
    llm = ConcurrentLLM(invalid_html)
    client.app.state.llm = llm
    source = _source(client)
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    first_client = TestClient(client.app)
    second_client = TestClient(client.app)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            first_client.post, f"/api/guides/{guide['guide_id']}/generate"
        )
        assert llm.first_started.wait(timeout=2)
        second_future = executor.submit(
            second_client.post, f"/api/guides/{guide['guide_id']}/generate"
        )
        assert not llm.second_started.wait(timeout=0.25)
        llm.release_first.set()
        first = first_future.result(timeout=5)
        second = second_future.result(timeout=5)

    artifact = client.get(f"/api/guides/{guide['guide_id']}/artifact.html")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "ok"
    assert second.json()["status"] == "ok"
    assert llm.calls == 1
    assert b"<title>Diagram</title>" in artifact.content
    assert b"Rejected" not in artifact.content


def test_rename_waits_for_generation_and_survives_after_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = ConcurrentLLM("<html><body>rejected</body></html>")
    client.app.state.llm = llm
    source = _source(client)
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    rename_started = Event()
    real_update_guide = app_module.update_guide

    def track_rename(jobs_dir, record, **changes):
        if "name" in changes:
            rename_started.set()
        return real_update_guide(jobs_dir, record, **changes)

    monkeypatch.setattr(app_module, "update_guide", track_rename)
    first_client = TestClient(client.app)
    second_client = TestClient(client.app)

    with ThreadPoolExecutor(max_workers=2) as executor:
        generation = executor.submit(
            first_client.post, f"/api/guides/{guide['guide_id']}/generate"
        )
        assert llm.first_started.wait(timeout=2)
        rename = executor.submit(
            second_client.patch,
            f"/api/guides/{guide['guide_id']}",
            json={"name": "Renamed While Generating"},
        )
        assert not rename_started.wait(timeout=0.25)
        llm.release_first.set()
        generated = generation.result(timeout=5)
        renamed = rename.result(timeout=5)

    assert generated.json()["status"] == "ok"
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed While Generating"
    assert client.get(f"/api/guides/{guide['guide_id']}").json()["name"] == (
        "Renamed While Generating"
    )


def test_delete_waits_for_generation_and_cleans_up_the_source(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = ConcurrentLLM("<html><body>rejected</body></html>")
    client.app.state.llm = llm
    source = _source(client)
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    delete_started = Event()
    real_delete_guide = app_module.delete_guide

    def track_delete(jobs_dir, guide_id):
        delete_started.set()
        return real_delete_guide(jobs_dir, guide_id)

    monkeypatch.setattr(app_module, "delete_guide", track_delete)
    first_client = TestClient(client.app)
    second_client = TestClient(client.app)

    with ThreadPoolExecutor(max_workers=2) as executor:
        generation = executor.submit(
            first_client.post, f"/api/guides/{guide['guide_id']}/generate"
        )
        assert llm.first_started.wait(timeout=2)
        deletion = executor.submit(
            second_client.delete, f"/api/guides/{guide['guide_id']}"
        )
        assert not delete_started.wait(timeout=0.25)
        llm.release_first.set()
        generated = generation.result(timeout=5)
        removed = deletion.result(timeout=5)

    assert generated.json()["status"] == "ok"
    assert removed.status_code == 200
    assert client.get(f"/api/guides/{guide['guide_id']}").status_code == 404
    assert client.get(f"/api/sources/{source['source_id']}").status_code == 404


def test_concurrent_revisions_serialize_and_preserve_both_updates(tmp_path: Path) -> None:
    client = _client(tmp_path, [LLMReply(text=GOOD)])
    pdf = tmp_path / "course.pdf"
    client.app.state.source_pdf = pdf
    source = client.post("/api/sources", json={"paths": [str(pdf)]}).json()
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    assert client.post(f"/api/guides/{guide['guide_id']}/generate").json()["status"] == "ok"
    llm = ConcurrentRevisionLLM()
    client.app.state.llm = llm
    first_client = TestClient(client.app)
    second_client = TestClient(client.app)
    first_body = {"selected_text": "first", "instruction": "first", "mode": "clarify"}
    second_body = {"selected_text": "second", "instruction": "second", "mode": "clarify"}

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_revision = executor.submit(
            first_client.post,
            f"/api/guides/{guide['guide_id']}/revisions",
            json=first_body,
        )
        assert llm.first_started.wait(timeout=2)
        second_revision = executor.submit(
            second_client.post,
            f"/api/guides/{guide['guide_id']}/revisions",
            json=second_body,
        )
        assert not llm.second_started.wait(timeout=0.25)
        llm.release_first.set()
        first = first_revision.result(timeout=5)
        second = second_revision.result(timeout=5)

    final = client.get(f"/api/guides/{guide['guide_id']}")
    artifact = client.get(f"/api/guides/{guide['guide_id']}/artifact.html")

    assert first.status_code == 200
    assert second.status_code == 200
    assert sorted([first.json()["revision_count"], second.json()["revision_count"]]) == [1, 2]
    assert final.json()["revision_count"] == 2
    assert b"First revision" in artifact.content
    assert b"Second revision" in artifact.content


def test_failed_retry_keeps_previous_artifact_bytes(tmp_path: Path) -> None:
    client = _client(tmp_path, [LLMError("retry failed", retryable=False)])
    pdf = tmp_path / "course.pdf"
    client.app.state.source_pdf = pdf
    source = client.post("/api/sources", json={"paths": [str(pdf)]}).json()
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    guide_record = load_guide(client.app.state.settings.jobs_dir, guide["guide_id"])
    final_path = client.app.state.settings.jobs_dir / "guides" / guide["guide_id"] / "artifact.html"
    previous_artifact = b"<html><body>verified artifact</body></html>"
    final_path.write_bytes(previous_artifact)
    save_guide(client.app.state.settings.jobs_dir, replace(guide_record, status="failed"))

    failed = client.post(f"/api/guides/{guide['guide_id']}/retry")

    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    assert final_path.read_bytes() == previous_artifact


def test_failed_retry_preserves_a_user_renamed_guide(tmp_path: Path) -> None:
    invalid_html = "<html><head><title>Rejected Candidate</title></head><body>invalid</body></html>"
    client = _client(
        tmp_path,
        [LLMError("initial failure", retryable=False), LLMReply(text=invalid_html), LLMReply(text=invalid_html)],
    )
    pdf = tmp_path / "course.pdf"
    client.app.state.source_pdf = pdf
    source = client.post("/api/sources", json={"paths": [str(pdf)]}).json()
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    failed = client.post(f"/api/guides/{guide['guide_id']}/generate")
    renamed = client.patch(
        f"/api/guides/{guide['guide_id']}", json={"name": "My Saved Guide"}
    )

    retried = client.post(f"/api/guides/{guide['guide_id']}/retry")

    assert failed.json()["status"] == "failed"
    assert renamed.json()["name"] == "My Saved Guide"
    assert retried.status_code == 200
    assert retried.json()["status"] == "needs-attention"
    assert retried.json()["name"] == "My Saved Guide"


@pytest.mark.parametrize("unverified_status", ["failed", "needs-attention"])
def test_unverified_guides_hide_and_reject_stale_artifacts(
    client: TestClient, unverified_status: str
) -> None:
    source = _source(client)
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    guide_record = load_guide(client.app.state.settings.jobs_dir, guide["guide_id"])
    final_path = client.app.state.settings.jobs_dir / "guides" / guide["guide_id"] / "artifact.html"
    final_path.write_bytes(b"stale artifact")
    save_guide(client.app.state.settings.jobs_dir, replace(guide_record, status=unverified_status))

    detail = client.get(f"/api/guides/{guide['guide_id']}")
    inline = client.get(f"/api/guides/{guide['guide_id']}/artifact.html?download=0")
    download = client.get(f"/api/guides/{guide['guide_id']}/artifact.html?download=1")

    assert detail.status_code == 200
    assert detail.json()["artifact_url"] is None
    assert inline.status_code == 409
    assert download.status_code == 409


def test_generation_uses_the_registered_copy_after_the_original_is_removed(
    client: TestClient,
) -> None:
    source = _source(client)
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    client.app.state.source_pdf.unlink()
    client.app.state.llm.replies.append(LLMReply(text=GOOD))

    generated = client.post(f"/api/guides/{guide['guide_id']}/generate")

    assert generated.status_code == 200
    assert generated.json()["status"] == "ok"


def test_rename_updates_detail_and_download_filename(client: TestClient) -> None:
    source = _source(client)
    guide = client.post(
        "/api/guides",
        json={
            "source_id": source["source_id"],
            "selection": {"mode": "custom", "start": 2, "end": 3},
        },
    ).json()
    client.app.state.llm.replies.append(LLMReply(text=GOOD))
    client.post(f"/api/guides/{guide['guide_id']}/generate")

    renamed = client.patch(
        f"/api/guides/{guide['guide_id']}",
        json={"name": "../Unsafe Guide"},
    )
    empty = client.patch(f"/api/guides/{guide['guide_id']}", json={"name": "   "})
    download = client.get(f"/api/guides/{guide['guide_id']}/artifact.html?download=1")

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "../Unsafe Guide"
    assert empty.json()["name"] == "../Unsafe Guide"
    assert "attachment" in download.headers["content-disposition"]
    assert ".." not in download.headers["content-disposition"]
    assert "/" not in download.headers["content-disposition"]
    assert "2-3" in download.headers["content-disposition"]
    inline = client.get(f"/api/guides/{guide['guide_id']}/artifact.html?download=0")
    assert inline.content == download.content


@pytest.mark.parametrize(
    ("reserved_name", "expected_filename"),
    [("COM2", "study-com2.html"), ("LPT9", "study-lpt9.html")],
)
def test_download_filename_protects_windows_reserved_names(
    client: TestClient, reserved_name: str, expected_filename: str
) -> None:
    source = _source(client)
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    client.app.state.llm.replies.append(LLMReply(text=GOOD))
    client.post(f"/api/guides/{guide['guide_id']}/generate")
    renamed = client.patch(
        f"/api/guides/{guide['guide_id']}",
        json={"name": reserved_name},
    )

    download = client.get(f"/api/guides/{guide['guide_id']}/artifact.html?download=1")

    assert renamed.status_code == 200
    assert f'filename="{expected_filename}"' in download.headers["content-disposition"]


def test_deleting_last_guide_removes_its_source_copy(client: TestClient) -> None:
    source = _source(client)
    body = {"source_id": source["source_id"], "selection": {"mode": "all"}}
    first = client.post("/api/guides", json=body).json()
    second = client.post("/api/guides", json=body).json()

    assert client.delete(f"/api/guides/{first['guide_id']}").status_code == 200
    assert client.get(f"/api/sources/{source['source_id']}").status_code == 200
    assert client.delete(f"/api/guides/{second['guide_id']}").status_code == 200
    assert client.get(f"/api/sources/{source['source_id']}").status_code == 404


def test_revision_replaces_artifact_and_increments_revision_count(tmp_path: Path) -> None:
    revised = GOOD.replace("<title>Diagram</title>", "<title>Revised Guide</title>")
    llm = QueueLLM([LLMReply(text=GOOD), LLMReply(text=revised)])
    client = _client(tmp_path, llm.replies)
    client.app.state.llm = llm
    pdf = tmp_path / "course.pdf"
    source = client.post("/api/sources", json={"paths": [str(pdf)]}).json()
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    client.post(f"/api/guides/{guide['guide_id']}/generate")
    generated_guide = client.get(f"/api/guides/{guide['guide_id']}").json()
    before = client.get(f"/api/guides/{guide['guide_id']}/artifact.html").content

    revised_response = client.post(
        f"/api/guides/{guide['guide_id']}/revisions",
        json={
            "selected_text": "the selected paragraph",
            "instruction": "Clarify the distinction",
            "mode": "clarify",
        },
    )

    assert revised_response.status_code == 200
    assert revised_response.json()["revision_count"] == 1
    assert revised_response.json()["guide_id"] == guide["guide_id"]
    assert revised_response.json()["name"] == generated_guide["name"]
    assert revised_response.json()["source_id"] == generated_guide["source_id"]
    assert len(client.get("/api/guides").json()) == 1
    after = client.get(f"/api/guides/{guide['guide_id']}/artifact.html").content
    assert after != before
    assert b"Revised Guide" in after
    revision_message = llm.seen[-1][1]["content"][-1]["text"]
    assert "the selected paragraph" in revision_message
    assert "Clarify the distinction" in revision_message


def test_failed_revision_keeps_the_previous_artifact_and_count(tmp_path: Path) -> None:
    llm = QueueLLM([LLMReply(text=GOOD), LLMError("revision failed", retryable=False)])
    client = _client(tmp_path, llm.replies)
    client.app.state.llm = llm
    pdf = tmp_path / "course.pdf"
    source = client.post("/api/sources", json={"paths": [str(pdf)]}).json()
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    client.post(f"/api/guides/{guide['guide_id']}/generate")
    before = client.get(f"/api/guides/{guide['guide_id']}/artifact.html").content

    failed = client.post(
        f"/api/guides/{guide['guide_id']}/revisions",
        json={"selected_text": "text", "instruction": "change", "mode": "custom"},
    )

    assert failed.status_code == 400
    assert client.get(f"/api/guides/{guide['guide_id']}/artifact.html").content == before
    assert client.get(f"/api/guides/{guide['guide_id']}").json()["revision_count"] == 0


def test_custom_revision_requires_instruction_but_clarify_can_use_empty_instruction(
    tmp_path: Path,
) -> None:
    revised = GOOD.replace("<title>Diagram</title>", "<title>Clarified Guide</title>")
    llm = QueueLLM([LLMReply(text=GOOD), LLMReply(text=revised), LLMReply(text=revised)])
    client = _client(tmp_path, llm.replies)
    client.app.state.llm = llm
    pdf = tmp_path / "course.pdf"
    source = client.post("/api/sources", json={"paths": [str(pdf)]}).json()
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    client.post(f"/api/guides/{guide['guide_id']}/generate")

    rejected = client.post(
        f"/api/guides/{guide['guide_id']}/revisions",
        json={"selected_text": "text", "instruction": "   ", "mode": "custom"},
    )
    clarified = client.post(
        f"/api/guides/{guide['guide_id']}/revisions",
        json={"selected_text": "text", "instruction": "", "mode": "clarify"},
    )

    assert rejected.status_code == 400
    assert "instruction" in rejected.json()["detail"].lower()
    assert clarified.status_code == 200
    assert clarified.json()["revision_count"] == 1


def test_checker_failure_without_findings_keeps_the_previous_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalid_html = "<html><head><title>Not a guide</title></head><body>invalid</body></html>"
    llm = QueueLLM(
        [LLMReply(text=GOOD), LLMReply(text=invalid_html), LLMReply(text=invalid_html)]
    )
    client = _client(tmp_path, llm.replies)
    client.app.state.llm = llm
    monkeypatch.setattr(
        "backend.api.app.revise_guide",
        lambda request, **kwargs: real_revise_guide(
            request,
            checker=lambda _path, _skill_dir: CheckResult(False, []),
            **kwargs,
        ),
    )
    pdf = tmp_path / "course.pdf"
    source = client.post("/api/sources", json={"paths": [str(pdf)]}).json()
    guide = client.post(
        "/api/guides",
        json={"source_id": source["source_id"], "selection": {"mode": "all"}},
    ).json()
    client.post(f"/api/guides/{guide['guide_id']}/generate")
    before = client.get(f"/api/guides/{guide['guide_id']}/artifact.html").content

    failed = client.post(
        f"/api/guides/{guide['guide_id']}/revisions",
        json={"selected_text": "text", "instruction": "change", "mode": "custom"},
    )

    assert failed.status_code == 400
    assert client.get(f"/api/guides/{guide['guide_id']}/artifact.html").content == before
    assert client.get(f"/api/guides/{guide['guide_id']}").json()["revision_count"] == 0
