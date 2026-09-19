import json
from dataclasses import replace
from pathlib import Path

import pymupdf
import pytest

from backend.generate.unit import UnitStatus
from backend.ingest.source import store_source
from backend.jobs.store import (
    delete_guide,
    jobs_dir_for,
    list_guides,
    list_history,
    load_guide,
    load_job,
    new_guide,
    new_job,
    save_guide,
    save_job,
    set_unit_status,
    write_atomic,
)


@pytest.fixture()
def fixture_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "source.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(path)
    doc.close()
    return path


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


def test_same_source_is_stored_once_but_guides_are_not_deduplicated(
    tmp_path: Path, fixture_pdf: Path
) -> None:
    first = store_source(tmp_path / "jobs", [fixture_pdf], "pdf")
    second = store_source(tmp_path / "jobs", [fixture_pdf], "pdf")

    assert first.source_id == second.source_id
    one = new_guide(tmp_path / "jobs", first, {"mode": "all"})
    two = new_guide(tmp_path / "jobs", second, {"mode": "all"})

    assert one.guide_id != two.guide_id


def test_guide_json_is_published_with_an_atomic_replace(tmp_path: Path, monkeypatch) -> None:
    source_path = tmp_path / "page.png"
    source_path.write_bytes(b"image")
    source = store_source(tmp_path / "jobs", [source_path], "images")
    replacements: list[tuple[Path, Path]] = []
    real_replace = __import__("os").replace

    def record_replace(source_path: Path, target_path: Path) -> None:
        source_file = Path(source_path)
        target_file = Path(target_path)
        if target_file.name == "guide.json":
            assert source_file.is_file()
            replacements.append((source_file, target_file))
        real_replace(source_path, target_path)

    monkeypatch.setattr("backend.jobs.store.os.replace", record_replace)

    new_guide(tmp_path / "jobs", source, {"mode": "images"})

    assert len(replacements) == 1
    assert not replacements[0][0].exists()


def test_failed_guides_are_listed_newest_first(tmp_path: Path) -> None:
    source_path = tmp_path / "page.png"
    source_path.write_bytes(b"image")
    source = store_source(tmp_path / "jobs", [source_path], "images")
    older = new_guide(tmp_path / "jobs", source, {"mode": "images"})
    newer = new_guide(tmp_path / "jobs", source, {"mode": "images"})

    save_guide(
        tmp_path / "jobs",
        replace(
            older,
            status="failed",
            error="generation failed",
            updated_at="2026-09-18T10:00:00+00:00",
        ),
    )
    save_guide(
        tmp_path / "jobs",
        replace(newer, updated_at="2026-09-18T11:00:00+00:00"),
    )

    guides = list_guides(tmp_path / "jobs")

    assert [guide.guide_id for guide in guides] == [newer.guide_id, older.guide_id]
    assert guides[1].status == "failed"
    assert guides[1].error == "generation failed"


def test_list_guides_skips_metadata_missing_required_fields(tmp_path: Path) -> None:
    source_path = tmp_path / "page.png"
    source_path.write_bytes(b"image")
    jobs_dir = tmp_path / "jobs"
    source = store_source(jobs_dir, [source_path], "images")
    valid = new_guide(jobs_dir, source, {"mode": "images"})
    malformed_id = "b" * 32
    malformed_dir = jobs_dir / "guides" / malformed_id
    malformed_dir.mkdir(parents=True)
    malformed = valid.to_dict()
    del malformed["updated_at"]
    (malformed_dir / "guide.json").write_text(json.dumps(malformed), encoding="utf-8")

    with pytest.raises(ValueError, match="missing required fields"):
        load_guide(jobs_dir, malformed_id)

    assert [guide.guide_id for guide in list_guides(jobs_dir)] == [valid.guide_id]


def test_legacy_multi_range_jobs_are_readable_history_not_v1_guides(tmp_path: Path) -> None:
    legacy_id = "a" * 32
    legacy_dir = tmp_path / legacy_id
    legacy_dir.mkdir()
    legacy_record = {
        "job_id": legacy_id,
        "source_pdf": "C:/old/course.pdf",
        "page_count": 12,
        "units": [
            {
                "unit_id": "unit-01",
                "label": "Part one",
                "page_start": 1,
                "page_end": 4,
                "status": "ok",
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
    (legacy_dir / "job.json").write_text(json.dumps(legacy_record), encoding="utf-8")

    history = list_history(tmp_path)

    assert len(history) == 1
    entry = history[0]
    assert entry.kind == "legacy"
    assert entry.job_id == legacy_id
    assert entry.status == "legacy"
    assert "not compatible" in entry.message.lower()
    assert list_guides(tmp_path) == []
    assert json.loads((legacy_dir / "job.json").read_text(encoding="utf-8")) == legacy_record


def test_source_copy_is_removed_after_the_last_guide_is_deleted(tmp_path: Path) -> None:
    source_path = tmp_path / "page.png"
    source_path.write_bytes(b"image")
    jobs_dir = tmp_path / "jobs"
    source = store_source(jobs_dir, [source_path], "images")
    first = new_guide(jobs_dir, source, {"mode": "images"})
    second = new_guide(jobs_dir, source, {"mode": "images"})
    source_dir = jobs_dir / "sources" / source.source_id

    delete_guide(jobs_dir, first.guide_id)
    assert source_dir.is_dir()

    delete_guide(jobs_dir, second.guide_id)
    assert not source_dir.exists()
