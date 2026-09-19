from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal, TypedDict

SourceKind = Literal["pdf", "images"]

LEGACY_HISTORY_MESSAGE = (
    "This older multi-range job is not compatible with Study Forge v1. "
    "Choose the source again to create a new guide."
)

INTERRUPTED_GUIDE_MESSAGE = "Guide generation was interrupted. Retry to continue."


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
