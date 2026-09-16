from __future__ import annotations

import io
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
                with Image.open(target) as existing:
                    images.append(PageImage(number, target, *existing.size))
                continue
            pixmap = doc[number - 1].get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM))
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            image.thumbnail((max_edge, max_edge), Image.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=quality, optimize=True)
            target.write_bytes(buffer.getvalue())
            images.append(PageImage(number, target, *image.size))
    return images
