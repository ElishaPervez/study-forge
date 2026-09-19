from __future__ import annotations

import filecmp
import hashlib
import json
import os
import shutil
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from backend.ingest.pdf import InputImage, normalize_image, page_count, rasterize
from backend.jobs.schema import SourceAsset, SourceKind, _validate_stored_name
from backend.jobs.store import write_atomic

SOURCE_MANIFEST_NAME = "source.json"
SOURCE_DIR_NAME = "sources"
SOURCE_ID_LENGTH = 64
IMAGE_MEDIA_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def source_dir_for(jobs_dir: Path, source_id: str) -> Path:
    _validate_source_id(source_id)
    return jobs_dir / SOURCE_DIR_NAME / source_id


def store_source(root: Path, paths: Sequence[Path], kind: SourceKind) -> SourceAsset:
    source_paths = [Path(path) for path in paths]
    _validate_inputs(source_paths, kind)

    source_id = _content_id(source_paths, kind)
    source_dir = source_dir_for(root, source_id)
    source_dir_exists = source_dir.exists()
    existing = _load_existing(source_dir, source_id)
    if existing is not None:
        if existing.kind != kind:
            raise ValueError("source identity belongs to a different source kind")
        return _repair_existing_source(source_paths, existing, source_dir)

    # v1 records created before source kind became part of the identity remain
    # reusable when their manifest confirms the same kind. A legacy record of a
    # different kind is intentionally left alone so byte-identical PDF and
    # image inputs cannot share it.
    legacy_id = _legacy_content_id(source_paths)
    if legacy_id != source_id and not source_dir_exists:
        legacy_dir = source_dir_for(root, legacy_id)
        legacy = _load_existing(legacy_dir, legacy_id)
        if (
            legacy is not None
            and legacy.kind == kind
            and legacy.files == _stored_names(source_paths)
        ):
            return _repair_existing_source(source_paths, legacy, legacy_dir)

    created_at = _now()
    asset = _asset_for_paths(source_paths, kind, source_id, created_at)

    source_dir.mkdir(parents=True, exist_ok=True)
    _remember_source_dir(asset, source_dir)
    try:
        for source_path, stored_name in zip(source_paths, asset.files, strict=True):
            _copy_atomic(source_path, source_dir / stored_name)
        write_atomic(
            source_dir / SOURCE_MANIFEST_NAME,
            json.dumps(asset.to_dict(), indent=2).encode("utf-8"),
        )
    except Exception:
        if not (source_dir / SOURCE_MANIFEST_NAME).exists():
            shutil.rmtree(source_dir, ignore_errors=True)
        raise
    return asset


def _validate_inputs(paths: list[Path], kind: SourceKind) -> None:
    if kind not in {"pdf", "images"}:
        raise ValueError(f"unsupported source kind: {kind!r}")
    if not paths:
        raise ValueError("at least one source file is required")
    if kind == "pdf" and len(paths) != 1:
        raise ValueError("a PDF source must contain exactly one file")
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"source file does not exist: {path}")
    suffixes = {path.suffix.casefold() for path in paths}
    if kind == "pdf":
        if suffixes != {".pdf"}:
            raise ValueError("a PDF source must contain a .pdf file")
        return
    if ".pdf" in suffixes:
        raise ValueError("mixed PDF and image source paths are not allowed")
    unsupported = sorted(suffix for suffix in suffixes if suffix not in IMAGE_MEDIA_TYPES)
    if unsupported:
        raise ValueError(f"unsupported image format: {unsupported[0] or '<none>'}")


def source_inputs(
    asset: SourceAsset,
    selection: Mapping[str, object],
    root: Path | None = None,
) -> list[InputImage]:
    """Resolve a stored source into the direct image inputs sent to the model."""
    stored_paths = _stored_paths(asset, root)
    mode = selection.get("mode")

    if asset.kind == "pdf":
        if len(stored_paths) != 1:
            raise ValueError("a PDF source must contain exactly one stored file")
        page_numbers = _selected_pages(stored_paths[0], asset, selection)
        cache_dir = _cache_dir(asset, stored_paths[0], root)
        return list(rasterize(stored_paths[0], page_numbers, cache_dir))

    if mode != "images":
        raise ValueError("image sources require an images selection")
    cache_dir = _image_cache_dir(asset, stored_paths[0], root)
    return [
        normalize_image(path, index, cache_dir)
        for index, path in enumerate(stored_paths, start=1)
    ]


def validate_stored_source(asset: SourceAsset, source_dir: Path) -> None:
    if asset.kind == "pdf" and len(asset.files) != 1:
        raise ValueError("a PDF source must contain exactly one stored file")
    if asset.kind == "images" and not asset.files:
        raise ValueError("an image source must contain at least one stored file")
    if (
        asset.kind == "images"
        and asset.image_count is not None
        and asset.image_count != len(asset.files)
    ):
        raise ValueError("stored image source count does not match its files")

    for stored_name in asset.files:
        _validate_stored_name(stored_name)
        path = source_dir / stored_name
        if not path.is_file():
            raise FileNotFoundError(f"stored source file does not exist: {path}")
        diagnostic = _stored_file_diagnostic(path, asset.kind)
        if diagnostic is not None:
            raise ValueError(f"stored source file is unreadable: {stored_name}: {diagnostic}")
        if asset.kind == "pdf" and asset.page_count is not None:
            actual_page_count = page_count(path)
            if actual_page_count != asset.page_count:
                raise ValueError("stored PDF page count does not match its manifest")


def _selected_pages(
    pdf_path: Path, asset: SourceAsset, selection: Mapping[str, object]
) -> list[int]:
    mode = selection.get("mode")
    if mode == "all":
        total = asset.page_count if asset.page_count is not None else page_count(pdf_path)
        return list(range(1, total + 1))
    if mode != "custom":
        raise ValueError("PDF sources require an all or custom selection")

    start = selection.get("start")
    end = selection.get("end")
    total = asset.page_count if asset.page_count is not None else page_count(pdf_path)
    if (
        isinstance(start, bool)
        or not isinstance(start, int)
        or isinstance(end, bool)
        or not isinstance(end, int)
        or start < 1
        or end < start
        or end > total
    ):
        raise ValueError("invalid PDF selection")
    return list(range(start, end + 1))


def _stored_paths(asset: SourceAsset, root: Path | None) -> list[Path]:
    source_dir = getattr(asset, "source_dir", None)
    if root is not None:
        source_dir = source_dir_for(root, asset.source_id)
    for candidate in (source_dir, getattr(asset, "_source_dir", None)):
        if candidate is not None:
            source_dir = Path(candidate)
            break

    paths: list[Path] = []
    for stored_name in asset.files:
        path = Path(stored_name)
        if not path.is_absolute() and source_dir is not None:
            path = source_dir / path
        if not path.is_file():
            raise FileNotFoundError(f"stored source file does not exist: {path}")
        paths.append(path)
    return paths


def _cache_dir(asset: SourceAsset, pdf_path: Path, root: Path | None) -> Path:
    source_dir = getattr(asset, "source_dir", None)
    if root is not None:
        source_dir = source_dir_for(root, asset.source_id)
    if source_dir is None:
        source_dir = getattr(asset, "_source_dir", None)
    if source_dir is not None:
        return Path(source_dir) / "pages"
    return pdf_path.parent / ".study-forge-pages"


def _image_cache_dir(asset: SourceAsset, image_path: Path, root: Path | None) -> Path:
    source_dir = getattr(asset, "source_dir", None)
    if root is not None:
        source_dir = source_dir_for(root, asset.source_id)
    if source_dir is None:
        source_dir = getattr(asset, "_source_dir", None)
    if source_dir is not None:
        return Path(source_dir) / "model-images"
    return image_path.parent / ".study-forge-model-images"


def _image_media_type(path: Path) -> str:
    try:
        return IMAGE_MEDIA_TYPES[path.suffix.casefold()]
    except KeyError:
        raise ValueError(f"unsupported image format: {path.suffix or '<none>'}") from None


def _remember_source_dir(asset: SourceAsset, source_dir: Path) -> None:
    # The persisted record intentionally stores portable file names. Keep the
    # current storage location on the in-memory record for immediate generation.
    asset.source_dir = source_dir
    asset._source_dir = source_dir


def _content_id(paths: Sequence[Path], kind: SourceKind) -> str:
    digest = hashlib.sha256()
    digest.update(kind.encode("ascii"))
    digest.update(b"\0")
    digest.update(len(paths).to_bytes(8, byteorder="big"))
    for path in paths:
        digest.update(path.stat().st_size.to_bytes(8, byteorder="big"))
        with path.open("rb") as source_file:
            for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _legacy_content_id(paths: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    digest.update(len(paths).to_bytes(8, byteorder="big"))
    for path in paths:
        digest.update(path.stat().st_size.to_bytes(8, byteorder="big"))
        with path.open("rb") as source_file:
            for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _stored_names(paths: Sequence[Path]) -> list[str]:
    names: list[str] = []
    used: set[str] = set()
    for index, path in enumerate(paths, start=1):
        name = path.name
        if name in used:
            name = f"{index:04d}-{name}"
        while name in used:
            name = f"{index:04d}-{name}"
        used.add(name)
        names.append(name)
    return names


def _copy_atomic(source: Path, target: Path) -> None:
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        with source.open("rb") as source_file, temporary.open("wb") as target_file:
            shutil.copyfileobj(source_file, target_file)
            target_file.flush()
            os.fsync(target_file.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _asset_for_paths(
    source_paths: Sequence[Path],
    kind: SourceKind,
    source_id: str,
    created_at: str,
) -> SourceAsset:
    return SourceAsset(
        source_id=source_id,
        kind=kind,
        display_name=source_paths[0].name,
        files=_stored_names(source_paths),
        page_count=page_count(source_paths[0]) if kind == "pdf" else None,
        image_count=len(source_paths) if kind == "images" else None,
        total_bytes=sum(path.stat().st_size for path in source_paths),
        created_at=created_at,
    )


def _load_existing(source_dir: Path, source_id: str) -> SourceAsset | None:
    for manifest_name in (SOURCE_MANIFEST_NAME, "manifest.json"):
        manifest = source_dir / manifest_name
        if not manifest.is_file():
            continue
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            asset = SourceAsset.from_dict(payload)
            if asset.source_id != source_id:
                raise ValueError("source manifest id does not match its directory")
        except (OSError, TypeError, ValueError):
            continue
        _remember_source_dir(asset, source_dir)
        if manifest_name != SOURCE_MANIFEST_NAME:
            write_atomic(
                source_dir / SOURCE_MANIFEST_NAME,
                json.dumps(asset.to_dict(), indent=2).encode("utf-8"),
            )
        return asset
    return None


def _repair_existing_source(
    source_paths: Sequence[Path], asset: SourceAsset, source_dir: Path
) -> SourceAsset:
    normalized = _asset_for_paths(source_paths, asset.kind, asset.source_id, asset.created_at)
    if len(asset.files) == len(source_paths) and len(set(asset.files)) == len(asset.files):
        normalized.files = list(asset.files)
    if asset.to_dict() != normalized.to_dict():
        _copy_source_files(source_paths, normalized, source_dir)
        _remember_source_dir(normalized, source_dir)
        write_atomic(
            source_dir / SOURCE_MANIFEST_NAME,
            json.dumps(normalized.to_dict(), indent=2).encode("utf-8"),
        )
        return normalized

    _copy_source_files(source_paths, asset, source_dir)
    return asset


def _copy_source_files(
    source_paths: Sequence[Path], asset: SourceAsset, source_dir: Path
) -> None:
    if len(source_paths) != len(asset.files):
        raise ValueError("stored source manifest does not match the selected files")

    for source_path, stored_name in zip(source_paths, asset.files, strict=True):
        stored_path = Path(stored_name)
        if stored_path.is_absolute() or stored_path.name != stored_name:
            raise ValueError("stored source manifest contains an unsafe file name")
        target = source_dir / stored_path
        if _stored_file_is_valid(target, asset.kind) and _files_match(source_path, target):
            continue
        _copy_atomic(source_path, target)


def _stored_file_is_valid(path: Path, kind: SourceKind) -> bool:
    return _stored_file_diagnostic(path, kind) is None


def _files_match(left: Path, right: Path) -> bool:
    try:
        return filecmp.cmp(left, right, shallow=False)
    except OSError:
        return False


def _stored_file_diagnostic(path: Path, kind: SourceKind) -> str | None:
    if not path.is_file():
        return "stored source file does not exist"
    try:
        if kind == "pdf":
            page_count(path)
        else:
            _image_media_type(path)
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                image.load()
    except Exception as error:  # noqa: BLE001 - every parser/library failure means unhealthy storage
        return str(error) or error.__class__.__name__
    return None


def _validate_source_id(source_id: str) -> None:
    if (
        len(source_id) != SOURCE_ID_LENGTH
        or not source_id.isascii()
        or any(character not in "0123456789abcdef" for character in source_id)
    ):
        raise ValueError(f"invalid source id: {source_id!r}")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")
