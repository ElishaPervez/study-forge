from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from backend.generate.unit import UnitStatus

JOB_ID_LENGTH = 32
RESERVED = {"CON", "PRN", "AUX", "NUL", "COM1", "LPT1"}


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
    if (
        len(job_id) != JOB_ID_LENGTH
        or not job_id.isascii()
        or not job_id.isalnum()
        or job_id != job_id.lower()
        or job_id.upper() in RESERVED
    ):
        raise ValueError(f"invalid job id: {job_id!r}")
    return root / job_id


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".job-tmp-{uuid.uuid4().hex}"
    try:
        temp.write_bytes(data)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


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
