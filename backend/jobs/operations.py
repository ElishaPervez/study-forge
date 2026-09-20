"""Durable request records and safe publication of completed guides.

A request is accepted and saved before the app reports it as queued. Completed
guides are written to a new versioned file first and then named by the guide
record in a single write, so an interruption can never present a mixture of the
old and the new completion information.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from backend.generate.unit import UnitStatus
from backend.jobs.schema import (
    INTERRUPTED_GUIDE_MESSAGE,
    INTERRUPTED_OPERATION_MESSAGE,
    GuideRecord,
    OperationKind,
    OperationRecord,
    _validated_record_id,
)
from backend.jobs.store import (
    INTERRUPTED_GUIDE_STATUSES,
    guide_dir_for,
    list_guides,
    update_guide,
    write_atomic,
)

OPERATIONS_DIR_NAME = "operations"
VERSIONS_DIR_NAME = "versions"
RECEIPT_LENGTH = 32
DEFAULT_COMPLETED_FILE = "artifact.html"
UNFINISHED_STATES = {"waiting", "running"}


def guide_id_for_receipt(receipt: str) -> str:
    """Derive a guide id from its submission receipt.

    An interrupted acceptance that repeats the same receipt therefore lands on the
    same guide instead of creating a second history entry.
    """
    _validate_receipt(receipt)
    return hashlib.sha256(f"study-forge-guide:{receipt}".encode("ascii")).hexdigest()[:32]


def operations_dir_for(jobs_dir: Path) -> Path:
    return jobs_dir / OPERATIONS_DIR_NAME


def operation_path_for(jobs_dir: Path, receipt: str) -> Path:
    _validate_receipt(receipt)
    return operations_dir_for(jobs_dir) / f"{receipt}.json"


def new_operation(
    *,
    guide_id: str,
    kind: OperationKind,
    order: int,
    receipt: str | None = None,
    selected_text: str | None = None,
    instruction: str | None = None,
    revision_mode: str | None = None,
    intended_revision: int = 0,
    base_fingerprint: str | None = None,
) -> OperationRecord:
    return OperationRecord(
        receipt=receipt or uuid.uuid4().hex,
        guide_id=guide_id,
        order=order,
        kind=kind,
        state="waiting",
        created_at=_now(),
        selected_text=selected_text,
        instruction=instruction,
        revision_mode=revision_mode,
        intended_revision=intended_revision,
        base_fingerprint=base_fingerprint,
    )


def save_operation(jobs_dir: Path, operation: OperationRecord) -> None:
    write_atomic(
        operation_path_for(jobs_dir, operation.receipt),
        json.dumps(operation.to_dict(), indent=2).encode("utf-8"),
    )


def update_operation(jobs_dir: Path, operation: OperationRecord, **changes: object) -> OperationRecord:
    updated = replace(operation, **changes)
    save_operation(jobs_dir, updated)
    return updated


def load_operation(jobs_dir: Path, receipt: str) -> OperationRecord:
    path = operation_path_for(jobs_dir, receipt)
    if not path.is_file():
        raise FileNotFoundError(f"no request {receipt!r}")
    return OperationRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))


def list_operations(jobs_dir: Path) -> list[OperationRecord]:
    operations_dir = operations_dir_for(jobs_dir)
    if not operations_dir.is_dir():
        return []
    operations: list[OperationRecord] = []
    for entry in operations_dir.iterdir():
        if not entry.is_file() or entry.suffix != ".json":
            continue
        try:
            operations.append(load_operation(jobs_dir, entry.stem))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            # A malformed request must not prevent healthy history from loading.
            continue
    return sorted(operations, key=lambda operation: (operation.order, operation.created_at))


def operations_for_guide(jobs_dir: Path, guide_id: str) -> list[OperationRecord]:
    return [
        operation for operation in list_operations(jobs_dir) if operation.guide_id == guide_id
    ]


def fingerprint_html(html: str | bytes) -> str:
    data = html.encode("utf-8") if isinstance(html, str) else html
    return hashlib.sha256(data).hexdigest()


def current_fingerprint(jobs_dir: Path, guide: GuideRecord) -> str | None:
    """Identify the completed version an edit was written against."""
    if guide.completed_fingerprint is not None:
        return guide.completed_fingerprint
    try:
        path = completed_file_path(jobs_dir, guide)
    except ValueError:
        return None
    if not path.is_file():
        return None
    return fingerprint_html(path.read_bytes())


def completed_file_path(jobs_dir: Path, guide: GuideRecord) -> Path:
    """Resolve the file the guide record names as its completed version."""
    name = DEFAULT_COMPLETED_FILE if guide.completed_file is None else guide.completed_file
    return guide_dir_for(jobs_dir, guide.guide_id) / _validated_completed_name(name)


def read_completed_html(jobs_dir: Path, guide: GuideRecord) -> str:
    return completed_file_path(jobs_dir, guide).read_text(encoding="utf-8")


def completed_file_exists(jobs_dir: Path, guide: GuideRecord) -> bool:
    try:
        return completed_file_path(jobs_dir, guide).is_file()
    except ValueError:
        return False


def publish_completed_guide(
    jobs_dir: Path,
    guide: GuideRecord,
    *,
    html: bytes,
    receipt: str,
    revision_count: int,
    name: str,
) -> GuideRecord:
    """Save a checked guide as a new version, then name it in one guide write."""
    _validate_receipt(receipt)
    guide_dir = guide_dir_for(jobs_dir, guide.guide_id)
    version_name = f"{VERSIONS_DIR_NAME}/{receipt}.html"
    write_atomic(guide_dir / VERSIONS_DIR_NAME / f"{receipt}.html", html)
    return update_guide(
        jobs_dir,
        guide,
        name=name,
        status=UnitStatus.OK.value,
        error=None,
        findings=[],
        revision_count=revision_count,
        completed_file=version_name,
        completed_request=receipt,
        completed_at=_now(),
        completed_fingerprint=fingerprint_html(html),
    )


def close_completed_request(jobs_dir: Path, operation: OperationRecord) -> OperationRecord:
    """A guide naming this request proves publishing succeeded; close the record."""
    return update_operation(
        jobs_dir,
        operation,
        state="completed",
        error=None,
        finished_at=_now(),
    )


def recover_operations(jobs_dir: Path) -> list[OperationRecord]:
    """One startup pass that decides which saved versions are authoritative."""
    guides = {guide.guide_id: guide for guide in list_guides(jobs_dir)}
    recovered: list[OperationRecord] = []

    for operation in list_operations(jobs_dir):
        if operation.tombstone or operation.state not in UNFINISHED_STATES:
            continue
        guide = guides.get(operation.guide_id)
        if guide is not None and guide.completed_request == operation.receipt:
            recovered.append(close_completed_request(jobs_dir, operation))
            continue
        recovered.append(
            update_operation(
                jobs_dir,
                operation,
                state="interrupted",
                error=INTERRUPTED_OPERATION_MESSAGE,
                finished_at=_now(),
            )
        )

    for guide in list_guides(jobs_dir):
        if guide.status not in INTERRUPTED_GUIDE_STATUSES:
            continue
        recovered_guide = update_guide(
            jobs_dir,
            guide,
            status=UnitStatus.FAILED.value,
            error=INTERRUPTED_GUIDE_MESSAGE,
            findings=[INTERRUPTED_GUIDE_MESSAGE],
        )
        guides[recovered_guide.guide_id] = recovered_guide

    _remove_abandoned_versions(jobs_dir, guides)
    return recovered


def tombstone_guide_operations(jobs_dir: Path, guide_id: str) -> None:
    """Keep only a receipt marker so a repeated submission cannot recreate a deleted guide."""
    for operation in operations_for_guide(jobs_dir, guide_id):
        if operation.tombstone:
            continue
        save_operation(
            jobs_dir,
            replace(
                operation,
                selected_text=None,
                instruction=None,
                base_fingerprint=None,
                tombstone=True,
            ),
        )


def _remove_abandoned_versions(jobs_dir: Path, guides: dict[str, GuideRecord]) -> None:
    guides_root = jobs_dir / "guides"
    if not guides_root.is_dir():
        return
    for entry in guides_root.iterdir():
        versions = entry / VERSIONS_DIR_NAME
        if not entry.is_dir() or not versions.is_dir():
            continue
        guide = guides.get(entry.name)
        authoritative = guide.completed_file if guide is not None else None
        for version in versions.iterdir():
            if not version.is_file():
                continue
            if authoritative == f"{VERSIONS_DIR_NAME}/{version.name}":
                continue
            version.unlink(missing_ok=True)
        if not any(versions.iterdir()):
            shutil.rmtree(versions, ignore_errors=True)


def _validated_completed_name(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise ValueError("guide record has an invalid completed file name")
    parts = name.split("/")
    if len(parts) == 1:
        _validate_version_file_name(parts[0])
        return parts[0]
    if len(parts) == 2 and parts[0] == VERSIONS_DIR_NAME:
        _validate_version_file_name(parts[1])
        return f"{VERSIONS_DIR_NAME}/{parts[1]}"
    raise ValueError("guide record has an unsafe completed file name")


def _validate_version_file_name(name: str) -> None:
    if (
        not name
        or name in {".", ".."}
        or "\x00" in name
        or "/" in name
        or "\\" in name
        or Path(name).name != name
    ):
        raise ValueError("guide record has an unsafe completed file name")


def _validate_receipt(receipt: str) -> None:
    _validated_record_id(receipt, "request", RECEIPT_LENGTH)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")
