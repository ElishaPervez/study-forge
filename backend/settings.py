from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash-20260910"


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str
    model: str
    reasoning_effort: str
    max_output_tokens: int
    skill_dir: Path
    jobs_dir: Path


def _read_env(env_file: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_file.is_file():
        return values
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def load_settings(env_file: Path | None = None) -> Settings:
    env = _read_env(env_file or Path(".env"))
    api_key = env.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is missing; put it in .env")
    return Settings(
        openrouter_api_key=api_key,
        model=env.get("MODEL", DEFAULT_MODEL),
        reasoning_effort=env.get("REASONING_EFFORT", "low"),
        max_output_tokens=int(env.get("MAX_OUTPUT_TOKENS", "32768")),
        skill_dir=Path(env.get("SKILL_DIR", "diagram-design")),
        jobs_dir=Path(env.get("JOBS_DIR", "jobs")),
    )
