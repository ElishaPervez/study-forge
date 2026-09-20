from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from backend.generate.unit import UnitStatus
from backend.jobs.schema import (
    LEGACY_HISTORY_MESSAGE,
    GuideRecord,
    ImageSelection,
    LegacyHistoryEntry,
    PdfSelection,
    Selection,
    SourceAsset,
)

JOB_ID_LENGTH = 32
RESERVED = {"CON", "PRN", "AUX", "NUL", "COM1", "LPT1"}
# On Windows a reader holding a record open makes the atomic replace fail with a
# sharing violation, so a transient failure is retried instead of surfacing.
REPLACE_RETRY_SECONDS = 2.0
REPLACE_RETRY_INTERVAL_SECONDS = 0.01
GUIDES_DIR_NAME = "guides"
GUIDE_METADATA_NAME = "guide.json"
INTERRUPTED_GUIDE_STATUSES = {
    UnitStatus.PENDING.value,
    UnitStatus.RUNNING.value,
    UnitStatus.VERIFYING.value,
    UnitStatus.REPAIRING.value,
}


@dataclass
class Unit:
    unit_id: str
    label: str
    page_start: int
    page_end: int
    status: UnitStatus = UnitStatus.PENDING

    @property
    def page_numbers(self) -> list[int]:
        return list(range(self.page_start, self.page_end + 1))


@dataclass
class Job:
    job_id: str
    source_pdf: str
    page_count: int
    units: list[Unit] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict) -> Job:
        units = [
            Unit(
                unit_id=item["unit_id"],
                label=item["label"],
                page_start=item["page_start"],
                page_end=item["page_end"],
                status=UnitStatus(item["status"]),
            )
            for item in payload["units"]
        ]
        return cls(payload["job_id"], payload["source_pdf"], payload["page_count"], units)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "source_pdf": self.source_pdf,
            "page_count": self.page_count,
            "units": [
                {
                    "unit_id": unit.unit_id,
                    "label": unit.label,
                    "page_start": unit.page_start,
                    "page_end": unit.page_end,
                    "status": unit.status.value,
                }
                for unit in self.units
            ],
        }


def jobs_dir_for(root: Path, job_id: str) -> Path:
    """Validate the id, then build the path. Never join model-supplied text blindly."""
    _validate_safe_id(job_id, "job")
    return root / job_id


def guide_dir_for(jobs_dir: Path, guide_id: str) -> Path:
    _validate_safe_id(guide_id, "guide")
    return jobs_dir / GUIDES_DIR_NAME / guide_id


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".job-tmp-{uuid.uuid4().hex}"
    try:
        temp.write_bytes(data)
        _replace_with_retry(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _replace_with_retry(temp: Path, path: Path) -> None:
    deadline = time.monotonic() + REPLACE_RETRY_SECONDS
    while True:
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(REPLACE_RETRY_INTERVAL_SECONDS)


def new_job(
    jobs_dir: Path,
    source_pdf: Path,
    page_count: int,
    ranges: list[tuple[str, int, int]],
) -> Job:
    for label, start, end in ranges:
        if start < 1 or end < start or end > page_count:
            raise ValueError(f"invalid range for {label!r}: {start}..{end} of {page_count}")
    job = Job(
        job_id=uuid.uuid4().hex,
        source_pdf=str(source_pdf),
        page_count=page_count,
        units=[
            Unit(unit_id=f"unit-{index + 1:02d}", label=label, page_start=start, page_end=end)
            for index, (label, start, end) in enumerate(ranges)
        ],
    )
    save_job(jobs_dir, job)
    return job


def save_job(jobs_dir: Path, job: Job) -> None:
    payload = asdict(job)
    for unit in payload["units"]:
        unit["status"] = unit["status"].value
    write_atomic(
        jobs_dir_for(jobs_dir, job.job_id) / "job.json",
        json.dumps(payload, indent=2).encode("utf-8"),
    )


def load_job(jobs_dir: Path, job_id: str) -> Job:
    path = jobs_dir_for(jobs_dir, job_id) / "job.json"
    if not path.is_file():
        raise FileNotFoundError(f"no job {job_id!r}")
    return Job.from_dict(json.loads(path.read_text(encoding="utf-8")))


def set_unit_status(jobs_dir: Path, job_id: str, unit_id: str, status: UnitStatus) -> None:
    job = load_job(jobs_dir, job_id)
    job.units = [replace(unit, status=status) if unit.unit_id == unit_id else unit for unit in job.units]
    save_job(jobs_dir, job)


def new_guide(
    jobs_dir: Path,
    source: SourceAsset,
    selection: Mapping[str, object],
    *,
    guide_id: str | None = None,
) -> GuideRecord:
    normalized_selection = _normalize_selection(source, selection)
    now = _now()
    guide = GuideRecord(
        guide_id=guide_id or uuid.uuid4().hex,
        source_id=source.source_id,
        selection=normalized_selection,
        name=source.display_name,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    save_guide(jobs_dir, guide)
    return guide


def save_guide(jobs_dir: Path, guide: GuideRecord) -> None:
    write_atomic(
        guide_dir_for(jobs_dir, guide.guide_id) / GUIDE_METADATA_NAME,
        json.dumps(guide.to_dict(), indent=2).encode("utf-8"),
    )


def update_guide(jobs_dir: Path, guide: GuideRecord, **changes: object) -> GuideRecord:
    updated = replace(guide, **changes, updated_at=_now())
    save_guide(jobs_dir, updated)
    return updated


def load_guide(jobs_dir: Path, guide_id: str) -> GuideRecord:
    path = guide_dir_for(jobs_dir, guide_id) / GUIDE_METADATA_NAME
    if not path.is_file():
        raise FileNotFoundError(f"no guide {guide_id!r}")
    return GuideRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))


def list_guides(jobs_dir: Path) -> list[GuideRecord]:
    guides_root = jobs_dir / GUIDES_DIR_NAME
    if not guides_root.is_dir():
        return []
    guides: list[GuideRecord] = []
    for entry in guides_root.iterdir():
        if not entry.is_dir() or not (entry / GUIDE_METADATA_NAME).is_file():
            continue
        try:
            guides.append(load_guide(jobs_dir, entry.name))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            # A malformed record must not prevent valid history from loading.
            continue
    return sorted(guides, key=lambda guide: guide.updated_at, reverse=True)


def list_legacy_history(jobs_dir: Path) -> list[LegacyHistoryEntry]:
    """Read old job records without converting them into v1 guide records."""
    if not jobs_dir.is_dir():
        return []

    entries: list[LegacyHistoryEntry] = []
    for directory in jobs_dir.iterdir():
        if (
            not directory.is_dir()
            or directory.name in {GUIDES_DIR_NAME, "sources"}
            or not (directory / "job.json").is_file()
        ):
            continue
        try:
            payload = json.loads((directory / "job.json").read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or "units" not in payload:
            continue

        timestamp = _file_timestamp(directory / "job.json")
        source_pdf = payload.get("source_pdf")
        name = Path(source_pdf).name if isinstance(source_pdf, str) and source_pdf else "Older job"
        entries.append(
            LegacyHistoryEntry(
                history_id=f"legacy-{directory.name}",
                job_id=directory.name,
                name=name,
                status="legacy",
                message=LEGACY_HISTORY_MESSAGE,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
    return sorted(entries, key=lambda entry: entry.updated_at, reverse=True)


def list_history(jobs_dir: Path) -> list[GuideRecord | LegacyHistoryEntry]:
    # Reading history has no side effects: startup recovery decides which saved
    # versions are authoritative, once, before any screen is served.
    entries: list[GuideRecord | LegacyHistoryEntry] = [
        *list_guides(jobs_dir),
        *list_legacy_history(jobs_dir),
    ]
    return sorted(entries, key=lambda entry: entry.updated_at, reverse=True)


def delete_guide(jobs_dir: Path, guide_id: str) -> None:
    guide = load_guide(jobs_dir, guide_id)
    guide_dir = guide_dir_for(jobs_dir, guide_id)
    shutil.rmtree(guide_dir)

    if any(item.source_id == guide.source_id for item in list_guides(jobs_dir)):
        return

    from backend.ingest.source import source_dir_for

    shutil.rmtree(source_dir_for(jobs_dir, guide.source_id), ignore_errors=True)


def _normalize_selection(source: SourceAsset, selection: Mapping[str, object]) -> Selection:
    mode = selection.get("mode")
    if source.kind == "images":
        if mode != "images":
            raise ValueError("image sources require an images selection")
        return ImageSelection(mode="images")

    if mode == "all":
        return PdfSelection(mode="all", start=None, end=None)
    if mode != "custom":
        raise ValueError("PDF sources require an all or custom selection")

    start = selection.get("start")
    end = selection.get("end")
    if (
        isinstance(start, bool)
        or not isinstance(start, int)
        or isinstance(end, bool)
        or not isinstance(end, int)
        or start < 1
        or end < start
        or (source.page_count is not None and end > source.page_count)
    ):
        raise ValueError("invalid PDF selection")
    return PdfSelection(mode="custom", start=start, end=end)


def _validate_safe_id(value: str, label: str) -> None:
    if (
        len(value) != JOB_ID_LENGTH
        or not value.isascii()
        or not value.isalnum()
        or value != value.lower()
        or value.upper() in RESERVED
    ):
        raise ValueError(f"invalid {label} id: {value!r}")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _file_timestamp(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="microseconds")
    except OSError:
        return _now()
