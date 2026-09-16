from pathlib import Path

import pymupdf
import pytest

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


def test_rasterize_is_idempotent(tmp_path: Path, fixture_pdf: Path) -> None:
    out_dir = tmp_path / "pages"
    first = rasterize(fixture_pdf, [1], out_dir)
    stamp = first[0].path.stat().st_mtime_ns

    second = rasterize(fixture_pdf, [1], out_dir)

    assert second[0].path.stat().st_mtime_ns == stamp
