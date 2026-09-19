from pathlib import Path

from backend.verify.self_check import run_self_check

SKILL_DIR = Path("diagram-design")


def test_shipped_template_passes() -> None:
    result = run_self_check(SKILL_DIR / "assets" / "template.html", SKILL_DIR)

    assert result.ok is True
    assert result.findings == []


def test_broken_svg_reports_findings(tmp_path: Path) -> None:
    broken = tmp_path / "broken.html"
    broken.write_text(
        '<html><body><svg viewBox="0 0 100 100"><rect width="10" height="10"/></svg></body></html>',
        encoding="utf-8",
    )

    result = run_self_check(broken, SKILL_DIR)

    assert result.ok is False
    assert any("role=img" in finding for finding in result.findings)


def test_missing_file_fails_without_raising(tmp_path: Path) -> None:
    result = run_self_check(tmp_path / "nope.html", SKILL_DIR)

    assert result.ok is False
    assert result.findings


def test_safe_guide_script_passes(tmp_path: Path) -> None:
    candidate = tmp_path / "guide.html"
    candidate.write_text(
        """<html><head><style>@media print { .control { display: none; } }</style></head>
        <body><svg role=\"img\" aria-labelledby=\"guide-title guide-desc\">
        <title id=\"guide-title\">Guide</title><desc id=\"guide-desc\">A guide.</desc>
        </svg><button class=\"control\">Next</button>
        <script data-guide-controls>document.querySelector('button').textContent = 'Done';</script>
        </body></html>""",
        encoding="utf-8",
    )

    result = run_self_check(candidate, SKILL_DIR)

    assert result.ok is True
    assert result.findings == []


def test_unsafe_guide_script_fails(tmp_path: Path) -> None:
    candidate = tmp_path / "unsafe.html"
    candidate.write_text(
        """<html><head></head><body><svg role=\"img\" aria-labelledby=\"guide-title guide-desc\">
        <title id=\"guide-title\">Guide</title><desc id=\"guide-desc\">A guide.</desc>
        </svg><script data-guide-controls>fetch('/api');</script></body></html>""",
        encoding="utf-8",
    )

    result = run_self_check(candidate, SKILL_DIR)

    assert result.ok is False
    assert any("network" in finding.lower() for finding in result.findings)
