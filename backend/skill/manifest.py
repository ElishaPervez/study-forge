from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.md$")
TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Reference:
    name: str
    title: str
    summary: str
    path: Path


def _summarize(text: str) -> str:
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and not line.startswith("**"):
            return line[:160]
    return ""


def build_manifest(references_dir: Path) -> dict[str, Reference]:
    manifest: dict[str, Reference] = {}
    for path in sorted(references_dir.glob("*.md")):
        if not NAME_RE.match(path.name):
            continue
        text = path.read_text(encoding="utf-8")
        match = TITLE_RE.search(text)
        manifest[path.name] = Reference(
            name=path.name,
            title=match.group(1) if match else path.stem,
            summary=_summarize(text),
            path=path,
        )
    return manifest


def get_reference(manifest: Mapping[str, Reference], name: str) -> Reference:
    """Look up by exact manifest key. Anything else is a KeyError, by design."""
    if name not in manifest:
        raise KeyError(f"unknown reference: {name!r}")
    return manifest[name]
