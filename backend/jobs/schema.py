from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal, TypedDict

SourceKind = Literal["pdf", "images"]

OperationKind = Literal["create", "retry", "clarify", "update"]
OperationState = Literal["waiting", "running", "completed", "failed", "interrupted"]

OPERATION_KINDS = frozenset({"create", "retry", "clarify", "update"})
OPERATION_STATES = frozenset({"waiting", "running", "completed", "failed", "interrupted"})

LEGACY_HISTORY_MESSAGE = (
    "This older multi-range job is not compatible with Study Forge v1. "
    "Choose the source again to create a new guide."
)

INTERRUPTED_GUIDE_MESSAGE = "Guide generation was interrupted. Retry to continue."

INTERRUPTED_OPERATION_MESSAGE = "The app stopped before this request finished. Retry to continue."


class PdfSelection(TypedDict, total=False):
    mode: Literal["all", "custom"]
    start: int | None
    end: int | None


class ImageSelection(TypedDict):
    mode: Literal["images"]


Selection = PdfSelection | ImageSelection


@dataclass
class SourceAsset:
    source_id: str
    kind: SourceKind
    display_name: str
    files: list[str]
    page_count: int | None
    image_count: int | None
    total_bytes: int
    created_at: str

    @property
    def stored_files(self) -> list[str]:
        return self.files

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "kind": self.kind,
            "display_name": self.display_name,
            "files": list(self.files),
            "page_count": self.page_count,
            "image_count": self.image_count,
            "total_bytes": self.total_bytes,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> SourceAsset:
        if not isinstance(payload, dict):
            raise TypeError("source manifest must be an object")
        required = {"source_id", "display_name", "total_bytes", "created_at"}
        missing = required.difference(payload)
        if missing:
            raise ValueError("source manifest is missing required fields")
        raw_files = payload.get("files", payload.get("stored_files", []))
        if not isinstance(raw_files, list) or not all(isinstance(item, str) for item in raw_files):
            raise ValueError("source manifest files must be a list of strings")
        for stored_name in raw_files:
            _validate_stored_name(stored_name)
        kind = payload.get("kind")
        if kind not in {"pdf", "images"}:
            raise ValueError("source manifest has an invalid kind")
        return cls(
            source_id=str(payload["source_id"]),
            kind=kind,
            display_name=str(payload["display_name"]),
            files=list(raw_files),
            page_count=_optional_int(payload.get("page_count")),
            image_count=_optional_int(payload.get("image_count")),
            total_bytes=int(payload["total_bytes"]),
            created_at=str(payload["created_at"]),
        )


def _validate_stored_name(stored_name: str) -> None:
    windows_path = PureWindowsPath(stored_name)
    if (
        not stored_name
        or stored_name in {".", ".."}
        or "\x00" in stored_name
        or "/" in stored_name
        or "\\" in stored_name
        or PurePosixPath(stored_name).is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or ".." in PurePosixPath(stored_name).parts
        or ".." in windows_path.parts
    ):
        raise ValueError("source manifest contains an unsafe stored file name")


def _validated_record_id(value: object, label: str, length: int) -> str:
    if (
        not isinstance(value, str)
        or len(value) != length
        or not value.isascii()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"guide record has an invalid {label} id")
    return value


@dataclass
class GuideRecord:
    guide_id: str
    source_id: str
    selection: Selection
    name: str
    status: str
    created_at: str
    updated_at: str
    error: str | None = None
    findings: list[str] = field(default_factory=list)
    revision_count: int = 0
    # Which saved version is authoritative, so a replacement is never a mixture of
    # old and new completion information. Old records keep using artifact.html.
    completed_file: str | None = None
    completed_request: str | None = None
    completed_at: str | None = None
    completed_fingerprint: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "guide_id": self.guide_id,
            "source_id": self.source_id,
            "selection": dict(self.selection),
            "name": self.name,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "findings": list(self.findings),
            "revision_count": self.revision_count,
            "completed_file": self.completed_file,
            "completed_request": self.completed_request,
            "completed_at": self.completed_at,
            "completed_fingerprint": self.completed_fingerprint,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> GuideRecord:
        if not isinstance(payload, dict):
            raise TypeError("guide record must be an object")
        required = {
            "guide_id",
            "source_id",
            "selection",
            "name",
            "status",
            "created_at",
            "updated_at",
        }
        missing = required.difference(payload)
        if missing:
            raise ValueError("guide record is missing required fields")
        guide_id = _validated_record_id(payload["guide_id"], "guide", 32)
        source_id = _validated_record_id(payload["source_id"], "source", 64)
        selection = payload.get("selection")
        if not isinstance(selection, dict):
            raise TypeError("guide record selection must be an object")
        findings = payload.get("findings", [])
        if not isinstance(findings, list) or not all(isinstance(item, str) for item in findings):
            raise ValueError("guide record findings must be a list of strings")
        error = payload.get("error")
        if error is not None and not isinstance(error, str):
            raise ValueError("guide record error must be a string or null")
        return cls(
            guide_id=guide_id,
            source_id=source_id,
            selection=dict(selection),
            name=str(payload["name"]),
            status=str(payload["status"]),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
            error=error,
            findings=list(findings),
            revision_count=int(payload.get("revision_count", 0)),
            completed_file=_optional_text(payload.get("completed_file"), "completed_file"),
            completed_request=_optional_text(
                payload.get("completed_request"), "completed_request"
            ),
            completed_at=_optional_text(payload.get("completed_at"), "completed_at"),
            completed_fingerprint=_optional_text(
                payload.get("completed_fingerprint"), "completed_fingerprint"
            ),
        )


@dataclass
class OperationRecord:
    """One accepted request; the durable side of the background queue."""

    receipt: str
    guide_id: str
    order: int
    kind: OperationKind
    state: OperationState
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    # Submitted edit payload for clarification and custom updates.
    selected_text: str | None = None
    instruction: str | None = None
    revision_mode: str | None = None
    intended_revision: int = 0
    base_fingerprint: str | None = None
    tombstone: bool = False

    @property
    def retry_available(self) -> bool:
        return not self.tombstone and self.state in {"failed", "interrupted"}

    @property
    def is_active(self) -> bool:
        return not self.tombstone and self.state in {"waiting", "running"}

    def summary(self) -> dict[str, object]:
        """The queue-facing view: never carries selected text or a full document."""
        return {
            "receipt": self.receipt,
            "guide_id": self.guide_id,
            "order": self.order,
            "kind": self.kind,
            "state": self.state,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "retry_available": self.retry_available,
            "error": self.error,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "receipt": self.receipt,
            "guide_id": self.guide_id,
            "order": self.order,
            "kind": self.kind,
            "state": self.state,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "selected_text": self.selected_text,
            "instruction": self.instruction,
            "revision_mode": self.revision_mode,
            "intended_revision": self.intended_revision,
            "base_fingerprint": self.base_fingerprint,
            "tombstone": self.tombstone,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> OperationRecord:
        if not isinstance(payload, dict):
            raise TypeError("operation record must be an object")
        required = {"receipt", "guide_id", "order", "kind", "state", "created_at"}
        missing = required.difference(payload)
        if missing:
            raise ValueError("operation record is missing required fields")
        kind = payload["kind"]
        if kind not in OPERATION_KINDS:
            raise ValueError("operation record has an invalid kind")
        state = payload["state"]
        if state not in OPERATION_STATES:
            raise ValueError("operation record has an invalid state")
        order = payload["order"]
        if isinstance(order, bool) or not isinstance(order, int) or order < 0:
            raise ValueError("operation record has an invalid order")
        intended_revision = payload.get("intended_revision", 0)
        if isinstance(intended_revision, bool) or not isinstance(intended_revision, int):
            raise TypeError("operation record has an invalid intended revision")
        return cls(
            receipt=_validated_record_id(payload["receipt"], "request", 32),
            guide_id=_validated_record_id(payload["guide_id"], "guide", 32),
            order=order,
            kind=kind,
            state=state,
            created_at=str(payload["created_at"]),
            started_at=_optional_text(payload.get("started_at"), "started_at"),
            finished_at=_optional_text(payload.get("finished_at"), "finished_at"),
            error=_optional_text(payload.get("error"), "error"),
            selected_text=_optional_text(payload.get("selected_text"), "selected_text"),
            instruction=_optional_text(payload.get("instruction"), "instruction"),
            revision_mode=_optional_text(payload.get("revision_mode"), "revision_mode"),
            intended_revision=intended_revision,
            base_fingerprint=_optional_text(payload.get("base_fingerprint"), "base_fingerprint"),
            tombstone=bool(payload.get("tombstone", False)),
        )


@dataclass(frozen=True)
class LegacyHistoryEntry:
    history_id: str
    job_id: str
    name: str
    status: Literal["legacy"]
    message: str
    created_at: str
    updated_at: str
    kind: Literal["legacy"] = "legacy"

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "history_id": self.history_id,
            "job_id": self.job_id,
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("source manifest count must be an integer or null")
    return value


def _optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"record {label} must be a string or null")
    return value
