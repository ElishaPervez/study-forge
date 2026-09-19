import os
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from backend.ingest.pdf import MAX_EDGE, page_count, preview_page, rasterize
from backend.ingest.source import source_inputs, store_source


@pytest.fixture()
def fixture_pdf(tmp_path: Path) -> Path:
    """Built here, never committed: no student material and no binary in git."""
    path = tmp_path / "source.pdf"
    doc = pymupdf.open()
    for number in range(5):
        page = doc.new_page(width=595, height=842)  # A4 portrait
        page.insert_text((72, 120), f"Page {number + 1}")
    doc.save(path)
    doc.close()
    return path


def test_page_count_reads_the_real_document(fixture_pdf: Path) -> None:
    assert page_count(fixture_pdf) == 5


def test_rasterize_produces_one_bounded_jpeg_per_page(tmp_path: Path, fixture_pdf: Path) -> None:
    images = rasterize(fixture_pdf, [2, 3], tmp_path / "pages")

    assert [image.page_number for image in images] == [2, 3]
    for image in images:
        assert image.path.is_file()
        assert image.path.suffix == ".jpg"
        assert max(image.width, image.height) <= MAX_EDGE


def test_rasterize_rejects_an_out_of_range_page_before_writing(
    tmp_path: Path, fixture_pdf: Path
) -> None:
    out_dir = tmp_path / "pages"

    with pytest.raises(ValueError, match="page 99"):
        rasterize(fixture_pdf, [1, 99], out_dir)

    assert not out_dir.exists() or not any(out_dir.iterdir())


def test_rasterize_rerenders_an_invalid_cached_jpeg(
    tmp_path: Path, fixture_pdf: Path
) -> None:
    out_dir = tmp_path / "pages"
    out_dir.mkdir()
    target = out_dir / "0001.jpg"
    target.write_bytes(b"interrupted JPEG bytes")

    images = rasterize(fixture_pdf, [1], out_dir)

    assert images[0].path == target
    with Image.open(target) as rendered:
        assert rendered.format == "JPEG"
        assert max(rendered.size) <= MAX_EDGE


def test_rasterize_rerenders_a_wrong_format_cached_image(
    tmp_path: Path, fixture_pdf: Path
) -> None:
    out_dir = tmp_path / "pages"
    out_dir.mkdir()
    target = out_dir / "0001.jpg"
    cached = Image.new("RGB", (100, 100), "white")
    cached.save(target, format="PNG")
    cached.close()

    rasterize(fixture_pdf, [1], out_dir)

    with Image.open(target) as rendered:
        assert rendered.format == "JPEG"


def test_rasterize_rerenders_an_oversized_cached_jpeg_within_requested_bound(
    tmp_path: Path, fixture_pdf: Path
) -> None:
    out_dir = tmp_path / "pages"
    out_dir.mkdir()
    target = out_dir / "0001.jpg"
    cached = Image.new("RGB", (MAX_EDGE + 1, 100), "white")
    cached.save(target, format="JPEG")
    cached.close()
    requested_max_edge = 256

    images = rasterize(fixture_pdf, [1], out_dir, max_edge=requested_max_edge)

    assert max(images[0].width, images[0].height) <= requested_max_edge
    with Image.open(target) as rendered:
        assert rendered.format == "JPEG"
        assert max(rendered.size) <= requested_max_edge


def test_rasterize_publishes_new_jpeg_with_atomic_replace(
    tmp_path: Path, fixture_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "pages"
    replacements: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def record_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        source_path = Path(source)
        target_path = Path(target)
        assert source_path.parent == out_dir
        assert source_path != target_path
        assert source_path.is_file()
        assert not target_path.exists()
        replacements.append((source_path, target_path))
        real_replace(source_path, target_path)

    monkeypatch.setattr(os, "replace", record_replace)

    images = rasterize(fixture_pdf, [1], out_dir)

    assert len(replacements) == 1
    assert replacements[0][1] == out_dir / "0001.jpg"
    assert not replacements[0][0].exists()
    assert images[0].path.is_file()


def test_rasterize_cleans_up_temporary_file_when_atomic_replace_fails(
    tmp_path: Path, fixture_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "pages"
    replacements: list[tuple[Path, Path]] = []

    def fail_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        source_path = Path(source)
        target_path = Path(target)
        replacements.append((source_path, target_path))
        assert source_path.parent == out_dir
        assert source_path.is_file()
        assert not target_path.exists()
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        rasterize(fixture_pdf, [1], out_dir)

    assert len(replacements) == 1
    assert not replacements[0][0].exists()
    assert not replacements[0][1].exists()
    assert not any(out_dir.iterdir())


def test_rasterize_is_idempotent(tmp_path: Path, fixture_pdf: Path) -> None:
    out_dir = tmp_path / "pages"
    first = rasterize(fixture_pdf, [1], out_dir)
    stamp = first[0].path.stat().st_mtime_ns

    second = rasterize(fixture_pdf, [1], out_dir)

    assert second[0].path.stat().st_mtime_ns == stamp


def test_preview_page_uses_bounded_jpeg_in_a_preview_cache(
    tmp_path: Path, fixture_pdf: Path
) -> None:
    preview = preview_page(fixture_pdf, 2, tmp_path / "previews")

    assert preview.page_number == 2
    assert preview.path.parent.name == "previews"
    assert preview.path.suffix == ".jpg"
    assert max(preview.width, preview.height) <= MAX_EDGE
    with Image.open(preview.path) as image:
        assert image.format == "JPEG"


def test_source_inputs_renders_only_the_requested_inclusive_pdf_pages(
    tmp_path: Path, fixture_pdf: Path
) -> None:
    source = store_source(tmp_path / "jobs", [fixture_pdf], "pdf")

    inputs = source_inputs(source, {"mode": "custom", "start": 2, "end": 4})

    assert [item.ordinal for item in inputs] == [2, 3, 4]
    assert [item.media_type for item in inputs] == ["image/jpeg"] * 3
    assert [item.source_label for item in inputs] == ["Page 2", "Page 3", "Page 4"]
    assert all(item.path.is_file() for item in inputs)


def test_source_inputs_normalizes_images_to_bounded_jpegs(tmp_path: Path) -> None:
    png = tmp_path / "first.png"
    webp = tmp_path / "second.webp"
    Image.new("RGB", (2000, 1000), (25, 75, 125)).save(png, format="PNG")
    Image.new("RGB", (800, 600), (100, 150, 200)).save(webp, format="WEBP")
    original_png = png.read_bytes()
    source = store_source(tmp_path / "jobs", [png, webp], "images")

    inputs = source_inputs(source, {"mode": "images"})

    assert [item.ordinal for item in inputs] == [1, 2]
    assert [item.media_type for item in inputs] == ["image/jpeg", "image/jpeg"]
    assert [item.source_label for item in inputs] == ["first.png", "second.webp"]
    for item in inputs:
        with Image.open(item.path) as image:
            assert image.format == "JPEG"
            assert max(image.size) <= MAX_EDGE
    # Stored originals stay untouched; only the model-facing cache is compressed.
    assert png.read_bytes() == original_png


def test_source_inputs_keep_large_image_groups_under_a_request_budget(tmp_path: Path) -> None:
    # Camera-sized photos: the 21-image regression exceeded the provider
    # gateway's request body limit (~96 MB base64). Normalized model inputs
    # must stay far below it even with base64's 4/3 overhead.
    photos: list[Path] = []
    for index in range(21):
        photo = tmp_path / f"photo-{index:02d}.jpg"
        Image.new("RGB", (3000, 4000), (20 * index % 255, 80, 160)).save(
            photo, format="JPEG", quality=95
        )
        photos.append(photo)
    source = store_source(tmp_path / "jobs", photos, "images")

    inputs = source_inputs(source, {"mode": "images"})

    raw_bytes = sum(item.path.stat().st_size for item in inputs)
    base64_bytes = raw_bytes * 4 // 3
    assert base64_bytes < 20 * 1024 * 1024


def test_source_inputs_reuse_the_normalized_image_cache(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (3000, 4000), (50, 100, 150)).save(photo, format="JPEG", quality=95)
    source = store_source(tmp_path / "jobs", [photo], "images")

    first = source_inputs(source, {"mode": "images"})
    stamp = first[0].path.stat().st_mtime_ns
    second = source_inputs(source, {"mode": "images"})

    assert second[0].path == first[0].path
    assert second[0].path.stat().st_mtime_ns == stamp


def test_store_source_rejects_mixed_pdf_and_image_paths(tmp_path: Path, fixture_pdf: Path) -> None:
    image = tmp_path / "page.png"
    image.write_bytes(b"image bytes")

    with pytest.raises(ValueError, match="mixed"):
        store_source(tmp_path / "jobs", [fixture_pdf, image], "images")


@pytest.mark.parametrize("suffix", [".bmp", ".tiff", ""])
def test_store_source_rejects_unsupported_image_before_copying(
    tmp_path: Path, suffix: str
) -> None:
    image = tmp_path / f"unsupported{suffix}"
    image.write_bytes(b"unsupported image bytes")
    jobs_dir = tmp_path / "jobs"

    with pytest.raises(ValueError, match="unsupported image format"):
        store_source(jobs_dir, [image], "images")

    assert not (jobs_dir / "sources").exists()
