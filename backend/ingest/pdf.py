from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image

from backend.diagnostics.trace import span

MAX_EDGE = 1536
QUALITY = 82
# 200 DPI before downscaling keeps small print legible at the 1536px bound.
ZOOM = 200 / 72


@dataclass(frozen=True)
class InputImage:
    ordinal: int
    path: Path
    media_type: str
    source_label: str

    def __init__(
        self,
        ordinal: int,
        path: Path,
        media_type: str,
        source_label: str | None = None,
        *,
        label: str | None = None,
    ) -> None:
        if source_label is None:
            source_label = label
        if not source_label:
            raise ValueError("an input image source label is required")
        object.__setattr__(self, "ordinal", ordinal)
        object.__setattr__(self, "path", Path(path))
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(self, "source_label", source_label)

    @property
    def label(self) -> str:
        return self.source_label


@dataclass(frozen=True, init=False)
class PageImage(InputImage):
    page_number: int
    width: int
    height: int

    def __init__(self, page_number: int, path: Path, width: int, height: int) -> None:
        InputImage.__init__(self, page_number, path, "image/jpeg", f"Page {page_number}")
        object.__setattr__(self, "page_number", page_number)
        object.__setattr__(self, "width", width)
        object.__setattr__(self, "height", height)


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


def normalize_image(
    image_path: Path,
    ordinal: int,
    out_dir: Path,
    max_edge: int = MAX_EDGE,
    quality: int = QUALITY,
) -> InputImage:
    """Prepare one stored image file for the model.

    Image sources are stored as the user's original files, which can be
    multi-megabyte camera photos; sent inline they overflow the provider
    gateway's request body limit. Reuse the PDF raster constraints (max edge,
    JPEG quality) and cache the result next to the source so the tool loop and
    retries stay cheap.
    """
    with span("ingest.normalize", ordinal=ordinal, source=image_path.name) as normalizing:
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{ordinal:04d}.jpg"
        if target.is_file():
            try:
                with Image.open(target) as existing:
                    if existing.format == "JPEG" and max(existing.size) <= max_edge:
                        existing.load()
                    else:
                        raise ValueError("cached image no longer matches the constraints")
                normalizing["cached"] = True
                return InputImage(ordinal, target, "image/jpeg", image_path.name)
            except (Image.DecompressionBombError, OSError, SyntaxError, ValueError):
                pass

        with Image.open(image_path) as opened:
            image = opened.convert("RGB")
        image.thumbnail((max_edge, max_edge), Image.LANCZOS)
        _write_jpeg(image, target, quality)
        normalizing["cached"] = False
        normalizing["source_bytes"] = image_path.stat().st_size
        normalizing["jpeg_bytes"] = target.stat().st_size
        return InputImage(ordinal, target, "image/jpeg", image_path.name)


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
            with span("ingest.page", page=number, pages=len(page_numbers)) as page_span:
                if target.is_file():
                    cached = _read_cached_page(number, target, max_edge)
                    if cached is not None:
                        page_span["cached"] = True
                        page_span["jpeg_bytes"] = target.stat().st_size
                        images.append(cached)
                        continue
                pixmap = doc[number - 1].get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM))
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                image.thumbnail((max_edge, max_edge), Image.LANCZOS)
                _write_jpeg(image, target, quality)
                page_span["cached"] = False
                page_span["pixels"] = f"{image.size[0]}x{image.size[1]}"
                page_span["jpeg_bytes"] = target.stat().st_size
                images.append(PageImage(number, target, *image.size))
    return images


def preview_page(
    pdf_path: Path,
    page_number: int,
    out_dir: Path,
    max_edge: int = MAX_EDGE,
    quality: int = QUALITY,
) -> PageImage:
    """Render one stored PDF page for the source viewer.

    The viewer gets its own cache directory, while the model-input path keeps
    using ``source_inputs`` and its existing raster cache unchanged.
    """
    return rasterize(pdf_path, [page_number], out_dir, max_edge=max_edge, quality=quality)[0]
