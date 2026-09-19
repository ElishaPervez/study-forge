from pathlib import Path

import pytest

from backend.skill.manifest import build_manifest, get_reference

SKILL_DIR = Path("diagram-design")


def test_manifest_finds_every_reference() -> None:
    manifest = build_manifest(SKILL_DIR / "references")

    assert len(manifest) == 53
    assert "type-architecture.md" in manifest
    assert manifest["type-architecture.md"].title == "Architecture"


def test_manifest_entries_never_escape_the_directory() -> None:
    manifest = build_manifest(SKILL_DIR / "references")
    root = (SKILL_DIR / "references").resolve()

    for reference in manifest.values():
        assert reference.path.resolve().parent == root


@pytest.mark.parametrize(
    "name",
    ["../.env", "..\\..\\.env", "/etc/passwd", "type-architecture.md/../style-guide.md",
     "TYPE-ARCHITECTURE.MD", "nope.md", ""],
)
def test_get_reference_refuses_anything_outside_the_manifest(name: str) -> None:
    manifest = build_manifest(SKILL_DIR / "references")

    with pytest.raises(KeyError):
        get_reference(manifest, name)
