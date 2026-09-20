from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
# This model supports only max/high/low — there is no "medium" — and "high" is
# its own default. A probe on a fixed task measured roughly 3.2k reasoning
# tokens at "low", 4.4k at "high" and 5.7k at "max", so the parameter is
# honored and "low" is the fastest valid setting. "high" is kept because it is
# the model working as intended; "low" is the lever if speed must beat quality.
DEFAULT_REASONING_EFFORT = "high"
# Reasoning and visible output share this budget, so it must cover both.
# DeepSeek's own ceiling is 384,000 and this cap sits below it. Even at "high"
# effort reasoning is headroom-hungry: on the previous model, 46,493 reasoning
# tokens on a 21-image source truncated a document mid-diagram at a 65,536 cap.
# A cap is a ceiling, not a charge.
#
# Lowering this cap cannot save time, and a probe proved it: at max_tokens=2000
# the model spent exactly 2000 tokens reasoning and returned no answer at all,
# because the two share one budget. A tight cap starves the document, not the
# thinking.
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
