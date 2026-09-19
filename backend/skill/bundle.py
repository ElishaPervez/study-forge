from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.skill.manifest import Reference, build_manifest

GATE_START = "## 0. First-time setup"
GATE_END = "## 1. Philosophy"


def strip_first_run_gate(skill_markdown: str) -> str:
    """Remove section 0: the app has already resolved the style guide once."""
    start = skill_markdown.find(GATE_START)
    end = skill_markdown.find(GATE_END)
    if start == -1 or end == -1 or end < start:
        return skill_markdown
    return skill_markdown[:start] + skill_markdown[end:]


@dataclass(frozen=True)
class Bundle:
    system_prompt: str
    references: dict[str, Reference]
    skill_dir: Path


def load_bundle(skill_dir: Path) -> Bundle:
    skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    style_guide = (skill_dir / "references" / "style-guide.md").read_text(encoding="utf-8")
    study_guide = (skill_dir / "references" / "study-guide.md").read_text(encoding="utf-8")
    system_prompt = (
        strip_first_run_gate(skill_md)
        + "\n\n---\n\n"
        + "# Active style guide (effective, do not ask about it)\n\n"
        + style_guide
        + "\n\n---\n\n"
        + "# Active study-guide system (effective, follow when producing a guide)\n\n"
        + study_guide
    )
    return Bundle(
        system_prompt=system_prompt,
        references=build_manifest(skill_dir / "references"),
        skill_dir=skill_dir,
    )
