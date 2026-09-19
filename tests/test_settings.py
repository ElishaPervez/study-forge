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
    assert settings.model == "meta/muse-spark-1.3-contributor"
    assert settings.reasoning_effort == "xhigh"
    assert settings.max_output_tokens == 200000


def test_load_settings_ignores_model_and_parameter_overrides(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "OPENROUTER_API_KEY=sk-test-123\n"
        "MODEL=~deepseek/deepseek-flash-latest\n"
        "REASONING_EFFORT=high\n"
        "MAX_OUTPUT_TOKENS=1\n",
        encoding="utf-8",
    )

    settings = load_settings(env_file=env)

    assert settings.model == "meta/muse-spark-1.3-contributor"
    assert settings.reasoning_effort == "xhigh"
    assert settings.max_output_tokens == 200000


def test_load_settings_rejects_missing_key(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("SKILL_DIR=diagram-design\n", encoding="utf-8")

    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        load_settings(env_file=env)
