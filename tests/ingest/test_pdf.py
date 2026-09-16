import os
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from backend.ingest.pdf import MAX_EDGE, page_count, rasterize


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
