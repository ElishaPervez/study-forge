from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image

MAX_EDGE = 1536
QUALITY = 82
# 200 DPI before downscaling keeps small print legible at the 1536px bound.
ZOOM = 200 / 72


@dataclass(frozen=True)
class PageImage:
    page_number: int
    path: Path
    width: int
    height: int


def _read_cached_page(number: int, target: Path, max_edge: int) -> PageImage | None:
    try:
        with Image.open(target) as existing:
            if existing.format != "JPEG" or max(existing.size) > max_edge:
                return None
            existing.load()
            width, height = existing.size
    except (Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        return None
    return PageImage(number, target, width, height)


def _write_jpeg(image: Image.Image, target: Path, quality: int) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent,
            prefix=f".{target.stem}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
        image.save(temporary_path, format="JPEG", quality=quality, optimize=True)
        os.replace(temporary_path, target)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def page_count(pdf_path: Path) -> int:
    with pymupdf.open(pdf_path) as doc:
        return doc.page_count


def rasterize(
    pdf_path: Path,
    page_numbers: Sequence[int],
    out_dir: Path,
    max_edge: int = MAX_EDGE,
    quality: int = QUALITY,
) -> list[PageImage]:
    total = page_count(pdf_path)
    for number in page_numbers:
        if number < 1 or number > total:
            raise ValueError(f"page {number} is outside 1..{total}")

    out_dir.mkdir(parents=True, exist_ok=True)
    images: list[PageImage] = []
    with pymupdf.open(pdf_path) as doc:
        for number in page_numbers:
            target = out_dir / f"{number:04d}.jpg"
            if target.is_file():
                cached = _read_cached_page(number, target, max_edge)
                if cached is not None:
                    images.append(cached)
                    continue
            pixmap = doc[number - 1].get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM))
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            image.thumbnail((max_edge, max_edge), Image.LANCZOS)
            _write_jpeg(image, target, quality)
            images.append(PageImage(number, target, *image.size))
    return images
