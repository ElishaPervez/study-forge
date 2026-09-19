from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

FINDING_PREFIX = "  - "
TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    findings: list[str] = field(default_factory=list)


def run_self_check(
    html_path: Path, skill_dir: Path, python_executable: str | None = None
) -> CheckResult:
    """Run the skill's shipped checker. Exit 0 = pass, 1 = at least one file failed."""
    script = skill_dir / "scripts" / "self_check.py"
    if not script.is_file():
        return CheckResult(False, [f"checker not found at {script}"])
    if not html_path.is_file():
        return CheckResult(False, [f"HTML file not found at {html_path}"])
    try:
        completed = subprocess.run(
            [python_executable or sys.executable, str(script), str(html_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(False, [f"checker timed out after {TIMEOUT_SECONDS}s"])
    except OSError as exc:
        return CheckResult(False, [f"could not run checker: {exc}"])
    if completed.returncode == 0:
        return CheckResult(True, [])
    findings = [
        line[len(FINDING_PREFIX):].strip()
        for line in completed.stdout.splitlines()
        if line.startswith(FINDING_PREFIX)
    ]
    if not findings:
        findings = [(completed.stderr or completed.stdout or "checker failed").strip()[:500]]
    return CheckResult(False, findings)
