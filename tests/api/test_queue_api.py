"""The queue, request and closing routes the desktop screen reads."""

import json
import time
from dataclasses import replace
from pathlib import Path
from threading import Event, Lock

import pymupdf
import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.jobs.store import load_guide, save_guide
from backend.llm.client import LLMReply
from backend.settings import Settings
from tests.api.helpers import (
    add_guide,
    receipt,
    submit_generation,
    submit_revision,
    track,
    wait_for_operation,
)

SKILL_DIR = Path("diagram-design")
GOOD = (SKILL_DIR / "assets" / "template.html").read_text(encoding="utf-8")


class GatedLLM:
    """Hold the first model call until the test releases it, then answer well."""

    def __init__(self) -> None:
        self.calls = 0
        self.first_started = Event()
        self.release_first = Event()
        self._calls_lock = Lock()

    def complete(self, messages, tools=None, *, on_text=None, stop=None) -> LLMReply:
        with self._calls_lock:
            self.calls += 1
            call_number = self.calls
        if call_number == 1:
            self.first_started.set()
            deadline = time.monotonic() + 5
            while not self.release_first.wait(timeout=0.05):
                if stop is not None and stop.is_set():
                    raise RuntimeError("the request was stopped")
                if time.monotonic() > deadline:
                    raise RuntimeError("test did not release the first request")
        reply = LLMReply(text=GOOD)
        if on_text is not None:
            on_text(reply.text)
        return reply


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    pdf = tmp_path / "course.pdf"
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 120), "Page 1")
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
    app = create_app(settings, llm=GatedLLM())
    app.state.source_pdf = pdf
    return track(TestClient(app))


def _source(client: TestClient) -> dict:
    return client.post(
        "/api/sources", json={"paths": [str(client.app.state.source_pdf)]}
    ).json()


def _verified_guide(client: TestClient, source_id: str) -> dict:
    """A history entry that already holds a checked guide, ready for updates."""
    guide = add_guide(client, source_id)
    jobs_dir = client.app.state.settings.jobs_dir
    artifact_path = jobs_dir / "guides" / guide["guide_id"] / "artifact.html"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(GOOD, encoding="utf-8")
    save_guide(jobs_dir, replace(load_guide(jobs_dir, guide["guide_id"]), status="ok"))
    return client.get(f"/api/guides/{guide['guide_id']}").json()


def test_queue_rows_name_the_guide_and_never_carry_submitted_text(
    client: TestClient,
) -> None:
    source = _source(client)
    holder = add_guide(client, source["source_id"])
    editor = _verified_guide(client, source["source_id"])
    _, holder_request = submit_generation(client, holder["guide_id"])
    assert client.app.state.llm.first_started.wait(timeout=5)

    _, update_request = submit_revision(
        client,
        editor["guide_id"],
        selected_text="a private sentence",
        instruction="make it clearer",
    )

    body = client.get("/api/queue").json()
    rows = {row["receipt"]: row for row in body["operations"]}
    detail = client.get(f"/api/operations/{update_request}").json()

    assert body["accepting"] is True
    assert body["closing"] is False
    assert body["change_number"] >= 1
    assert body["service_start"]
    assert rows[holder_request]["state"] == "running"
    assert rows[holder_request]["activity"]
    assert rows[holder_request]["guide_name"] == holder["name"]
    assert rows[update_request]["state"] == "waiting"
    assert rows[update_request]["kind"] == "update"
    assert rows[update_request]["guide_name"] == editor["name"]
    assert rows[update_request]["order"] > rows[holder_request]["order"]
    assert "a private sentence" not in json.dumps(body)
    assert "selected_text" not in detail
    assert "instruction" not in detail
    assert "a private sentence" not in json.dumps(detail)

    client.app.state.llm.release_first.set()
    assert wait_for_operation(client, holder_request)["state"] == "completed"
    assert wait_for_operation(client, update_request)["state"] == "completed"


def test_operation_route_rejects_unknown_and_malformed_receipts(client: TestClient) -> None:
    assert client.get("/api/operations/not-a-receipt").status_code == 400
    assert client.get(f"/api/operations/{receipt()}").status_code == 404
    assert (
        client.post(
            f"/api/operations/{receipt()}/retry", json={"receipt": receipt()}
        ).status_code
        == 404
    )


def test_a_waiting_guide_is_busy_for_rename_and_delete(client: TestClient) -> None:
    source = _source(client)
    running = add_guide(client, source["source_id"])
    waiting = add_guide(client, source["source_id"])

    _, running_request = submit_generation(client, running["guide_id"])
    assert client.app.state.llm.first_started.wait(timeout=5)
    _, waiting_request = submit_generation(client, waiting["guide_id"])
    rows = {row["receipt"]: row for row in client.get("/api/queue").json()["operations"]}
    assert rows[waiting_request]["state"] == "waiting"

    renamed = client.patch(
        f"/api/guides/{waiting['guide_id']}", json={"name": "Renamed Too Soon"}
    )
    removed = client.delete(f"/api/guides/{waiting['guide_id']}")

    assert renamed.status_code == 409
    assert removed.status_code == 409

    client.app.state.llm.release_first.set()
    assert wait_for_operation(client, running_request)["state"] == "completed"
    assert wait_for_operation(client, waiting_request)["state"] == "completed"
    assert (
        client.patch(
            f"/api/guides/{waiting['guide_id']}", json={"name": "Renamed Afterwards"}
        ).status_code
        == 200
    )


def test_closing_refuses_new_requests_and_interrupts_waiting_ones(client: TestClient) -> None:
    source = _source(client)
    running = add_guide(client, source["source_id"])
    waiting = add_guide(client, source["source_id"])

    _, running_request = submit_generation(client, running["guide_id"])
    assert client.app.state.llm.first_started.wait(timeout=5)
    _, waiting_request = submit_generation(client, waiting["guide_id"])

    prepared = client.post("/api/shutdown/prepare").json()
    refused = client.post(
        "/api/guides",
        json={
            "source_id": source["source_id"],
            "selection": {"mode": "all"},
            "receipt": receipt(),
        },
    )
    resumed = client.post("/api/shutdown/resume").json()

    assert prepared["accepting"] is False
    assert prepared["active"] >= 1
    assert prepared["waiting"] >= 1
    assert refused.status_code == 409
    assert resumed["accepting"] is True

    assert client.post("/api/shutdown/prepare").json()["accepting"] is False
    confirmed = client.post("/api/shutdown/confirm").json()

    assert confirmed["confirmed"] is True
    assert confirmed["accepting"] is False
    assert client.get(f"/api/operations/{waiting_request}").json()["state"] == "interrupted"
    # The running request stops as soon as it can, and is then recorded as interrupted.
    assert wait_for_operation(client, running_request)["state"] == "interrupted"
    assert client.get("/api/queue").json()["accepting"] is False
    interrupted = client.get(f"/api/guides/{waiting['guide_id']}").json()
    assert interrupted["retryable_request"]["receipt"] == waiting_request
    assert interrupted["retryable_request"]["state"] == "interrupted"


def test_closing_keeps_the_previous_version_of_a_guide_whose_update_lost(
    client: TestClient,
) -> None:
    source = _source(client)
    guide = _verified_guide(client, source["source_id"])
    before = client.get(f"/api/guides/{guide['guide_id']}/artifact.html").content

    _, request = submit_revision(client, guide["guide_id"])
    assert client.app.state.llm.first_started.wait(timeout=5)
    client.post("/api/shutdown/prepare")
    client.post("/api/shutdown/confirm")

    assert wait_for_operation(client, request)["state"] == "interrupted"
    kept = client.get(f"/api/guides/{guide['guide_id']}")
    assert kept.json()["status"] == "ok"
    assert kept.json()["revision_count"] == 0
    assert client.get(f"/api/guides/{guide['guide_id']}/artifact.html").content == before
