from pathlib import Path

from backend.skill.bundle import load_bundle

SKILL_DIR = Path("diagram-design")


def test_system_prompt_carries_the_style_guide_tokens() -> None:
    bundle = load_bundle(SKILL_DIR)

    assert "#2d3142" in bundle.system_prompt      # ink
    assert "#eb6c36" in bundle.system_prompt      # accent
    assert "Instrument Serif" in bundle.system_prompt


def test_system_prompt_omits_the_first_run_gate() -> None:
    bundle = load_bundle(SKILL_DIR)

    assert "First-time setup" not in bundle.system_prompt
    assert "style guide has been customized" not in bundle.system_prompt
    assert "## 1. Philosophy" in bundle.system_prompt


def test_system_prompt_is_deterministic() -> None:
    assert load_bundle(SKILL_DIR).system_prompt == load_bundle(SKILL_DIR).system_prompt
