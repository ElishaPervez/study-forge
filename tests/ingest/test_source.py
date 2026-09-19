import json
from io import BytesIO
from pathlib import Path

import pymupdf
from PIL import Image

import backend.ingest.source as source_module
from backend.ingest.source import store_source


def _valid_image_bytes(color: tuple[int, int, int]) -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), color).save(output, format="PNG")
    return output.getvalue()


def _write_pdf(path: Path, text: str) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 120), text)
    document.save(path)
    document.close()


def test_matching_source_content_reuses_one_local_copy(tmp_path: Path) -> None:
    image = tmp_path / "page.png"
    image.write_bytes(_valid_image_bytes((25, 75, 125)))
    jobs_dir = tmp_path / "jobs"

    first = store_source(jobs_dir, [image], "images")
    stored_dir = jobs_dir / "sources" / first.source_id
    stored_files_before = {
        path.name: path.stat().st_mtime_ns for path in stored_dir.iterdir() if path.is_file()
    }
    second = store_source(jobs_dir, [image], "images")

    assert second.source_id == first.source_id
    assert second.files == first.files
    assert {
        path.name: path.stat().st_mtime_ns for path in stored_dir.iterdir() if path.is_file()
    } == stored_files_before


def test_byte_identical_pdf_and_image_sources_use_different_ids(tmp_path: Path) -> None:
    pdf = tmp_path / "same.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(pdf)
    document.close()
    image = tmp_path / "same.png"
    image.write_bytes(pdf.read_bytes())
    jobs_dir = tmp_path / "jobs"

    pdf_source = store_source(jobs_dir, [pdf], "pdf")
    image_source = store_source(jobs_dir, [image], "images")

    assert pdf_source.source_id != image_source.source_id
    assert pdf_source.kind == "pdf"
    assert image_source.kind == "images"


def test_same_kind_reuses_a_kindless_v1_manifest(tmp_path: Path) -> None:
    image = tmp_path / "page.png"
    image.write_bytes(_valid_image_bytes((50, 100, 150)))
    jobs_dir = tmp_path / "jobs"
    current = store_source(jobs_dir, [image], "images")
    current_dir = jobs_dir / "sources" / current.source_id
    legacy_id = source_module._legacy_content_id([image])
    legacy_dir = jobs_dir / "sources" / legacy_id
    current_dir.rename(legacy_dir)
    manifest_path = legacy_dir / source_module.SOURCE_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_id"] = legacy_id
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    reused = store_source(jobs_dir, [image], "images")

    assert reused.source_id == legacy_id
    assert (legacy_dir / reused.files[0]).read_bytes() == image.read_bytes()
    assert not (jobs_dir / "sources" / current.source_id).exists()


def test_reregistering_a_source_repairs_only_a_missing_stored_file(tmp_path: Path, monkeypatch) -> None:
    first_image = tmp_path / "first.png"
    second_image = tmp_path / "second.png"
    first_image.write_bytes(_valid_image_bytes((75, 125, 175)))
    second_image.write_bytes(_valid_image_bytes((100, 150, 200)))
    jobs_dir = tmp_path / "jobs"

    source = store_source(jobs_dir, [first_image, second_image], "images")
    source_dir = jobs_dir / "sources" / source.source_id
    healthy_file = source_dir / source.files[0]
    missing_file = source_dir / source.files[1]
    healthy_bytes = healthy_file.read_bytes()
    missing_bytes = missing_file.read_bytes()
    missing_file.unlink()

    copied_targets: list[str] = []
    real_copy = source_module._copy_atomic

    def record_copy(source_path: Path, target_path: Path) -> None:
        copied_targets.append(target_path.name)
        real_copy(source_path, target_path)

    monkeypatch.setattr(source_module, "_copy_atomic", record_copy)

    repaired = store_source(jobs_dir, [first_image, second_image], "images")

    assert repaired.source_id == source.source_id
    assert healthy_file.read_bytes() == healthy_bytes
    assert missing_file.read_bytes() == missing_bytes
    assert copied_targets == [missing_file.name]


def test_reregistering_a_source_repairs_a_corrupt_stored_image(tmp_path: Path) -> None:
    image = tmp_path / "page.png"
    original_bytes = _valid_image_bytes((25, 75, 125))
    image.write_bytes(original_bytes)
    jobs_dir = tmp_path / "jobs"

    source = store_source(jobs_dir, [image], "images")
    stored_image = jobs_dir / "sources" / source.source_id / source.files[0]
    stored_image.write_bytes(b"not an image")

    repaired = store_source(jobs_dir, [image], "images")

    assert repaired.source_id == source.source_id
    assert stored_image.read_bytes() == original_bytes


def test_reregistering_a_source_replaces_a_valid_but_different_stored_image(tmp_path: Path) -> None:
    image = tmp_path / "page.png"
    original_bytes = _valid_image_bytes((25, 75, 125))
    replacement_bytes = _valid_image_bytes((125, 175, 225))
    image.write_bytes(original_bytes)
    jobs_dir = tmp_path / "jobs"

    source = store_source(jobs_dir, [image], "images")
    stored_image = jobs_dir / "sources" / source.source_id / source.files[0]
    stored_image.write_bytes(replacement_bytes)

    repaired = store_source(jobs_dir, [image], "images")

    assert repaired.source_id == source.source_id
    assert stored_image.read_bytes() == original_bytes


def test_reregistering_a_source_replaces_a_valid_but_different_stored_pdf(tmp_path: Path) -> None:
    pdf = tmp_path / "course.pdf"
    replacement = tmp_path / "replacement.pdf"
    _write_pdf(pdf, "original source")
    _write_pdf(replacement, "different valid source")
    jobs_dir = tmp_path / "jobs"

    source = store_source(jobs_dir, [pdf], "pdf")
    stored_pdf = jobs_dir / "sources" / source.source_id / source.files[0]
    stored_pdf.write_bytes(replacement.read_bytes())

    repaired = store_source(jobs_dir, [pdf], "pdf")

    assert repaired.source_id == source.source_id
    assert stored_pdf.read_bytes() == pdf.read_bytes()


def test_reregistering_a_pdf_rebuilds_a_manifest_with_no_files(tmp_path: Path) -> None:
    pdf = tmp_path / "course.pdf"
    _write_pdf(pdf, "source material")
    jobs_dir = tmp_path / "jobs"

    source = store_source(jobs_dir, [pdf], "pdf")
    source_dir = jobs_dir / "sources" / source.source_id
    manifest_path = source_dir / source_module.SOURCE_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = []
    manifest["page_count"] = 0
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    repaired = store_source(jobs_dir, [pdf], "pdf")

    assert repaired.source_id == source.source_id
    assert repaired.files == [pdf.name]
    assert (source_dir / pdf.name).read_bytes() == pdf.read_bytes()
    repaired_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert repaired_manifest["files"] == [pdf.name]
    assert repaired_manifest["page_count"] == repaired.page_count


def test_reregistering_images_rebuilds_a_manifest_with_the_wrong_file_count(
    tmp_path: Path,
) -> None:
    first_image = tmp_path / "first.png"
    second_image = tmp_path / "second.png"
    first_image.write_bytes(_valid_image_bytes((25, 75, 125)))
    second_image.write_bytes(_valid_image_bytes((125, 175, 225)))
    jobs_dir = tmp_path / "jobs"

    source = store_source(jobs_dir, [first_image, second_image], "images")
    source_dir = jobs_dir / "sources" / source.source_id
    manifest_path = source_dir / source_module.SOURCE_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = [first_image.name]
    manifest["image_count"] = 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    repaired = store_source(jobs_dir, [first_image, second_image], "images")

    assert repaired.source_id == source.source_id
    assert repaired.files == [first_image.name, second_image.name]
    assert (source_dir / first_image.name).read_bytes() == first_image.read_bytes()
    assert (source_dir / second_image.name).read_bytes() == second_image.read_bytes()
    repaired_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert repaired_manifest["files"] == [first_image.name, second_image.name]
    assert repaired_manifest["image_count"] == 2


def test_image_order_is_part_of_the_source_identity_and_copy_order(tmp_path: Path) -> None:
    first_image = tmp_path / "first.png"
    second_image = tmp_path / "second.png"
    first_image.write_bytes(b"first")
    second_image.write_bytes(b"second")
    jobs_dir = tmp_path / "jobs"

    forward = store_source(jobs_dir, [first_image, second_image], "images")
    reverse = store_source(jobs_dir, [second_image, first_image], "images")

    assert forward.source_id != reverse.source_id
    forward_bytes = [
        (jobs_dir / "sources" / forward.source_id / stored_file).read_bytes()
        for stored_file in forward.files
    ]
    reverse_bytes = [
        (jobs_dir / "sources" / reverse.source_id / stored_file).read_bytes()
        for stored_file in reverse.files
    ]
    assert forward_bytes == [b"first", b"second"]
    assert reverse_bytes == [b"second", b"first"]


def test_image_filenames_do_not_change_ordered_content_identity(tmp_path: Path) -> None:
    first_image = tmp_path / "first.png"
    second_image = tmp_path / "second.png"
    first_bytes = _valid_image_bytes((25, 75, 125))
    second_bytes = _valid_image_bytes((25, 75, 125))
    first_image.write_bytes(first_bytes)
    second_image.write_bytes(second_bytes)
    jobs_dir = tmp_path / "jobs"

    forward = store_source(jobs_dir, [first_image, second_image], "images")
    same_order = store_source(jobs_dir, [first_image, second_image], "images")
    reverse = store_source(jobs_dir, [second_image, first_image], "images")

    assert same_order.source_id == forward.source_id
    assert reverse.source_id == forward.source_id


def test_renamed_image_files_reuse_the_same_source_record(tmp_path: Path) -> None:
    original = tmp_path / "original.png"
    renamed = tmp_path / "renamed.png"
    original_bytes = _valid_image_bytes((25, 75, 125))
    original.write_bytes(original_bytes)
    renamed.write_bytes(original_bytes)
    jobs_dir = tmp_path / "jobs"

    first = store_source(jobs_dir, [original], "images")
    second = store_source(jobs_dir, [renamed], "images")

    assert second.source_id == first.source_id


def test_file_boundaries_are_part_of_the_source_identity(tmp_path: Path) -> None:
    joined_left = tmp_path / "joined-left.png"
    joined_right = tmp_path / "joined-right.png"
    split_left = tmp_path / "split-left.png"
    split_right = tmp_path / "split-right.png"
    joined_left.write_bytes(b"ab")
    joined_right.write_bytes(b"c")
    split_left.write_bytes(b"a")
    split_right.write_bytes(b"bc")

    first = store_source(tmp_path / "jobs", [joined_left, joined_right], "images")
    second = store_source(tmp_path / "jobs", [split_left, split_right], "images")

    assert first.source_id != second.source_id
