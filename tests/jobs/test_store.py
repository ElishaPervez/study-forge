from pathlib import Path

import pytest

from backend.generate.unit import UnitStatus
from backend.jobs.store import (
    jobs_dir_for,
    load_job,
    new_job,
    save_job,
    set_unit_status,
    write_atomic,
)


def test_new_job_creates_units_from_ranges(tmp_path: Path) -> None:
    job = new_job(tmp_path, Path("source.pdf"), 20, [("Unit 1", 1, 5), ("Unit 2", 6, 12)])

    assert len(job.units) == 2
    assert job.units[0].page_start == 1 and job.units[0].page_end == 5
    assert all(unit.status is UnitStatus.PENDING for unit in job.units)
    assert (tmp_path / job.job_id / "job.json").is_file()


def test_job_round_trips_through_disk(tmp_path: Path) -> None:
    job = new_job(tmp_path, Path("source.pdf"), 20, [("Unit 1", 1, 5)])

    save_job(tmp_path, job)
    reloaded = load_job(tmp_path, job.job_id)

    assert reloaded == job


def test_status_update_persists(tmp_path: Path) -> None:
    job = new_job(tmp_path, Path("source.pdf"), 20, [("Unit 1", 1, 5)])

    set_unit_status(tmp_path, job.job_id, job.units[0].unit_id, UnitStatus.OK)

    assert load_job(tmp_path, job.job_id).units[0].status is UnitStatus.OK


@pytest.mark.parametrize("job_id", ["../escape", "..", "a/b", "", "CON", "job id"])
def test_job_ids_that_could_escape_the_root_are_refused(tmp_path: Path, job_id: str) -> None:
    with pytest.raises(ValueError):
        jobs_dir_for(tmp_path, job_id)


def test_job_id_is_generated_not_supplied(tmp_path: Path) -> None:
    job = new_job(tmp_path, Path("source.pdf"), 20, [("Unit 1", 1, 5)])

    assert jobs_dir_for(tmp_path, job.job_id).parent.resolve() == tmp_path.resolve()


def test_unknown_job_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_job(tmp_path, "0" * 32)


def test_new_job_rejects_ranges_outside_one_based_inclusive_pages(tmp_path: Path) -> None:
    for page_range in [("Unit", 0, 2), ("Unit", 2, 1), ("Unit", 1, 21)]:
        with pytest.raises(ValueError):
            new_job(tmp_path, Path("source.pdf"), 20, [page_range])

    assert not list(tmp_path.iterdir())


def test_unit_page_numbers_include_both_range_endpoints(tmp_path: Path) -> None:
    job = new_job(tmp_path, Path("source.pdf"), 20, [("Unit 1", 2, 4)])

    assert job.units[0].page_numbers == [2, 3, 4]


def test_write_atomic_replaces_without_partial_reads(tmp_path: Path) -> None:
    target = tmp_path / "artifact.html"
    target.write_text("old", encoding="utf-8")

    write_atomic(target, b"new")

    assert target.read_bytes() == b"new"
    assert not list(tmp_path.glob(".job-tmp-*"))


def test_write_atomic_cleans_temp_when_replace_fails(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "artifact.html"
    target.write_text("old", encoding="utf-8")

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("backend.jobs.store.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        write_atomic(target, b"new")

    assert target.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob(".job-tmp-*"))
