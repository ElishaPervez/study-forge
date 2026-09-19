from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "meta/muse-spark-1.3-contributor"
# This provider exposes max/xhigh/high/medium/low/minimal (default: medium) and
# marks reasoning mandatory, so "none" is rejected outright.
DEFAULT_REASONING_EFFORT = "max"
# Reasoning and visible output share this budget, so it must cover both. The
# model's own ceiling is 943,718. "max" effort allocates roughly 95% of this
# budget to reasoning, which is more headroom-hungry than the previous model at
# "high": there, 46,493 reasoning tokens on a 21-image source truncated a
# document mid-diagram at a 65,536 cap. A cap is a ceiling, not a charge.
DEFAULT_MAX_OUTPUT_TOKENS = 200000


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
        model=DEFAULT_MODEL,
        reasoning_effort=DEFAULT_REASONING_EFFORT,
        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
        skill_dir=Path(env.get("SKILL_DIR", "diagram-design")),
        jobs_dir=Path(env.get("JOBS_DIR", "jobs")),
    )
