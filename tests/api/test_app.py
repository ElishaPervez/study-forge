from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.api.app import create_app
from backend.llm.client import LLMReply
from backend.settings import Settings
from tests.api.helpers import add_guide, track

SKILL_DIR = Path("diagram-design")
GOOD = (SKILL_DIR / "assets" / "template.html").read_text(encoding="utf-8")


class QueueLLM:
    def __init__(self, replies: list[object] | None = None) -> None:
        self.replies = list(replies or [])
        self.seen: list[list[dict]] = []

    def complete(self, messages, tools=None, *, on_text=None, stop=None) -> LLMReply:
        self.seen.append(list(messages))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if on_text is not None:
            on_text(reply.text)
        return reply


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    pdf = tmp_path / "source.pdf"
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
    app = create_app(settings, llm=QueueLLM())
    app.state.source_pdf = pdf
    app.state.source_image = tmp_path / "page.png"
    Image.new("RGB", (2, 2), (25, 75, 125)).save(app.state.source_image, format="PNG")
    return track(TestClient(app))


def test_source_registration_reuses_storage_and_reports_current_files(
    client: TestClient,
) -> None:
    path = str(client.app.state.source_pdf)

    first = client.post("/api/sources", json={"paths": [path]})
    second = client.post("/api/sources", json={"paths": [path]})

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["source_id"] == first.json()["source_id"]
    assert second.json()["files"] == first.json()["files"]
    assert second.json()["file_details"][0]["name"] == "source.pdf"

    source_id = first.json()["source_id"]
    detail = client.get(f"/api/sources/{source_id}")
    assert detail.status_code == 200
    assert detail.json()["page_count"] == 3
    assert detail.json()["file_details"][0]["exists"] is True


def test_source_registration_accepts_one_pdf_or_an_image_group_but_not_mixed(
    client: TestClient,
) -> None:
    pdf = str(client.app.state.source_pdf)
    image = str(client.app.state.source_image)

    assert client.post("/api/sources", json={"paths": [pdf, pdf]}).status_code == 400
    assert client.post("/api/sources", json={"paths": [pdf, image]}).status_code == 400

    images = client.post("/api/sources", json={"paths": [image, image]})
    assert images.status_code == 200
    assert images.json()["kind"] == "images"
    assert images.json()["image_count"] == 2


def test_pdf_page_preview_is_available_from_the_stored_source(client: TestClient) -> None:
    source = client.post(
        "/api/sources", json={"paths": [str(client.app.state.source_pdf)]}
    ).json()

    preview = client.get(f"/api/sources/{source['source_id']}/pages/2")

    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("image/jpeg")
    assert preview.content.startswith(b"\xff\xd8")


def test_source_delete_removes_unreferenced_source_and_keeps_referenced_source(
    client: TestClient,
) -> None:
    source = client.post(
        "/api/sources", json={"paths": [str(client.app.state.source_image)]}
    ).json()
    guide = add_guide(client, source["source_id"], {"mode": "images"})

    retained = client.delete(f"/api/sources/{source['source_id']}")

    assert retained.status_code == 200
    assert retained.json()["source_id"] == source["source_id"]
    assert retained.json()["retained"] is True
    assert client.get(f"/api/guides/{guide['guide_id']}").status_code == 200

    client.delete(f"/api/guides/{guide['guide_id']}")
    assert client.get(f"/api/sources/{source['source_id']}").status_code == 404

    unreferenced = client.post(
        "/api/sources", json={"paths": [str(client.app.state.source_image)]}
    ).json()
    removed = client.delete(f"/api/sources/{unreferenced['source_id']}")

    assert removed.status_code == 200
    assert removed.json()["deleted"] is True
    assert client.get(f"/api/sources/{source['source_id']}").status_code == 404
