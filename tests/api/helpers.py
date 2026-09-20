"""Helpers for the accepted-request contract.

Actions are accepted, recorded, and answered straight away; the single worker
finishes them a moment later. Tests therefore submit through the API and then
wait for the request row to settle instead of expecting the work in the reply.
"""

from __future__ import annotations

import time
import uuid

from fastapi.testclient import TestClient

from backend.jobs.schema import SourceAsset
from backend.jobs.store import new_guide

ACTIVE_STATES = {"waiting", "running"}
_CREATED: list[TestClient] = []


def receipt() -> str:
    return uuid.uuid4().hex


def track(client: TestClient) -> TestClient:
    """Remember an app-owning client so its worker is stopped after the test."""
    _CREATED.append(client)
    return client


def close_tracked_clients() -> None:
    while _CREATED:
        client = _CREATED.pop()
        queue = getattr(client.app.state, "queue", None)
        if queue is not None:
            queue.stop()


def wait_for_operation(client: TestClient, request: str, *, timeout: float = 15.0) -> dict:
    """Wait until an accepted request settles and return its queue row."""
    deadline = time.monotonic() + timeout
    while True:
        response = client.get(f"/api/operations/{request}")
        assert response.status_code == 200, response.text
        row = response.json()
        if row["state"] not in ACTIVE_STATES:
            return row
        if time.monotonic() > deadline:
            raise AssertionError(f"request {request} never settled: {row}")
        time.sleep(0.02)


def submit_guide(
    client: TestClient,
    source_id: str,
    selection: dict | None = None,
    *,
    request: str | None = None,
) -> tuple[dict, str]:
    """Accept a new guide and return its accepted view plus the request id."""
    request = request or receipt()
    response = client.post(
        "/api/guides",
        json={
            "source_id": source_id,
            "selection": selection or {"mode": "all"},
            "receipt": request,
        },
    )
    assert response.status_code == 202, response.text
    return response.json(), request


def forge(
    client: TestClient,
    source_id: str,
    selection: dict | None = None,
    *,
    request: str | None = None,
) -> dict:
    """Accept a new guide, wait for it to settle, and return its detail view."""
    accepted, request = submit_guide(client, source_id, selection, request=request)
    wait_for_operation(client, request)
    return client.get(f"/api/guides/{accepted['guide_id']}").json()


def add_guide(
    client: TestClient,
    source_id: str,
    selection: dict | None = None,
) -> dict:
    """Save a history entry directly, for tests that are not about acceptance."""
    source = SourceAsset.from_dict(client.get(f"/api/sources/{source_id}").json())
    record = new_guide(
        client.app.state.settings.jobs_dir, source, selection or {"mode": "all"}
    )
    return client.get(f"/api/guides/{record.guide_id}").json()


def submit_revision(
    client: TestClient,
    guide_id: str,
    *,
    selected_text: str = "the selected paragraph",
    instruction: str = "Clarify this.",
    mode: str = "custom",
    request: str | None = None,
) -> tuple[dict, str]:
    request = request or receipt()
    response = client.post(
        f"/api/guides/{guide_id}/revisions",
        json={
            "selected_text": selected_text,
            "instruction": instruction,
            "mode": mode,
            "receipt": request,
        },
    )
    assert response.status_code == 202, response.text
    return response.json(), request


def revise(
    client: TestClient,
    guide_id: str,
    *,
    selected_text: str = "the selected paragraph",
    instruction: str = "Clarify this.",
    mode: str = "custom",
    request: str | None = None,
) -> dict:
    """Accept an update, wait for it to settle, and return its request row."""
    _, request = submit_revision(
        client,
        guide_id,
        selected_text=selected_text,
        instruction=instruction,
        mode=mode,
        request=request,
    )
    return wait_for_operation(client, request)


def submit_generation(client: TestClient, guide_id: str) -> tuple[dict, str]:
    request = receipt()
    response = client.post(
        f"/api/guides/{guide_id}/generate", json={"receipt": request}
    )
    assert response.status_code == 202, response.text
    return response.json(), request


def submit_retry(client: TestClient, guide_id: str) -> tuple[dict, str]:
    request = receipt()
    response = client.post(f"/api/guides/{guide_id}/retry", json={"receipt": request})
    assert response.status_code == 202, response.text
    return response.json(), request


def retry_operation(client: TestClient, request: str) -> tuple[dict, str]:
    new_request = receipt()
    response = client.post(
        f"/api/operations/{request}/retry", json={"receipt": new_request}
    )
    assert response.status_code == 202, response.text
    return response.json(), new_request
