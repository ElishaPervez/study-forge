from pathlib import Path

import pytest

from backend.settings import load_settings


def test_load_settings_reads_key_and_pins_model(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "OPENROUTER_API_KEY=sk-test-123\nSKILL_DIR=diagram-design\n",
        encoding="utf-8",
    )

    settings = load_settings(env_file=env)

    assert settings.openrouter_api_key == "sk-test-123"
    assert settings.model == "deepseek/deepseek-v4.1-flash-20260910"
    assert settings.reasoning_effort == "low"
    assert settings.max_output_tokens == 32768


def test_load_settings_rejects_missing_key(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("SKILL_DIR=diagram-design\n", encoding="utf-8")

    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        load_settings(env_file=env)
