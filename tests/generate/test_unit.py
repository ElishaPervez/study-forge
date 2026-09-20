import json
from pathlib import Path
from threading import Event

import pytest

from backend.diagnostics.trace import Trace, trace_for, use_trace
from backend.fonts.embed import inject_fonts
from backend.generate.progress import ProgressReporter, count_document_lines
from backend.generate.unit import (
    FINDING_LOG_LIMIT,
    FINDING_LOG_MAX,
    MAX_CALLS,
    MAX_PATCHES,
    MAX_TOOL_TURNS,
    PATCH_TOOL,
    UnitRequest,
    UnitStatus,
    _apply_edits,
    _patched_document,
    generate_unit,
    logged_findings,
)
from backend.ingest.pdf import PageImage
from backend.llm.client import LLMError, LLMReply, ToolCall
from backend.skill.bundle import load_bundle
from backend.verify.self_check import CheckResult, run_self_check

SKILL_DIR = Path("diagram-design")
RAW_GOOD = (SKILL_DIR / "assets" / "template.html").read_text(encoding="utf-8")
GOOD = inject_fonts(RAW_GOOD, "")
MOTION = inject_fonts(
    (SKILL_DIR / "assets" / "template-motion.html").read_text(encoding="utf-8"), ""
)
BROKEN = '<html><body><svg viewBox="0 0 100 100"></svg></body></html>'


class ScriptedLLM:
    """Replays a fixed script and records the messages and tools it was shown."""

    def __init__(self, replies: list[LLMReply]) -> None:
        self._replies = list(replies)
        self.seen: list[list[dict]] = []
        self.tools: list[list[dict] | None] = []

    def complete(self, messages, tools=None, *, on_text=None, stop=None) -> LLMReply:
        self.seen.append(list(messages))
        self.tools.append(list(tools) if tools else None)
        reply = self._replies.pop(0)
        if on_text is not None:
            on_text(reply.text)
        return reply


def _request(tmp_path: Path, numbers=(1, 2)) -> UnitRequest:
    pages = []
    for number in numbers:
        path = tmp_path / f"{number:04d}.jpg"
        path.write_bytes(b"\xff\xd8\xff\xe0x")
        pages.append(PageImage(number, path, 100, 140))
    return UnitRequest("unit-1", "Cell Division", list(numbers), pages)


def test_single_call_unit_that_passes_stops_at_one_call(tmp_path: Path) -> None:
    llm = ScriptedLLM([LLMReply(text=RAW_GOOD)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.status is UnitStatus.OK
    assert result.calls == 1
    written = result.artifact_path.read_text(encoding="utf-8")
    # read_text normalizes newlines, so autocrlf cannot make this flaky.
    assert written == GOOD.strip() + "\n"
    assert "fonts.googleapis.com" not in written
    assert run_self_check(result.artifact_path, SKILL_DIR).ok is True


def test_tool_call_then_artifact_uses_two_calls(tmp_path: Path) -> None:
    tool_call = ToolCall("call_1", "read_reference", '{"name":"type-process.md"}')
    llm = ScriptedLLM([LLMReply(text="", tool_calls=[tool_call]), LLMReply(text=GOOD)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.calls == 2
    assert result.requested_refs == ["type-process.md"]
    assert result.status is UnitStatus.OK
    tool_message = [m for m in llm.seen[1] if m.get("role") == "tool"]
    assert tool_message and "Process" in tool_message[0]["content"]


def test_unknown_reference_name_is_refused_not_read(tmp_path: Path) -> None:
    tool_call = ToolCall("call_1", "read_reference", '{"name":"../../.env"}')
    llm = ScriptedLLM([LLMReply(text="", tool_calls=[tool_call]), LLMReply(text=GOOD)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.requested_refs == []
    tool_message = [m for m in llm.seen[1] if m.get("role") == "tool"]
    assert "unknown reference" in tool_message[0]["content"]


def test_broken_artifact_is_repaired_once(tmp_path: Path) -> None:
    llm = ScriptedLLM([LLMReply(text=BROKEN), LLMReply(text=GOOD)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.calls == 2
    assert result.status is UnitStatus.OK
    repair_prompt = llm.seen[1][-1]["content"]
    assert "role=img" in repair_prompt


def test_second_failure_marks_needs_attention_and_keeps_html(tmp_path: Path) -> None:
    llm = ScriptedLLM([LLMReply(text=BROKEN), LLMReply(text=BROKEN), LLMReply(text=GOOD)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.calls == 2
    assert len(llm.seen) == 2
    assert result.status is UnitStatus.NEEDS_ATTENTION
    assert result.findings
    assert result.artifact_path.is_file()


def test_never_exceeds_the_call_budget(tmp_path: Path) -> None:
    tool_call = ToolCall("call_1", "read_reference", '{"name":"type-architecture.md"}')
    llm = ScriptedLLM([LLMReply(text="", tool_calls=[tool_call])] * 6)

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.calls == MAX_TOOL_TURNS
    assert len(llm.seen) == MAX_TOOL_TURNS
    assert result.status is UnitStatus.NEEDS_ATTENTION
    assert result.artifact_path is None
    assert "allowance" in result.findings[0].lower()


def test_caller_limit_is_clamped_to_the_global_budget(tmp_path: Path) -> None:
    llm = ScriptedLLM([LLMReply(text=BROKEN)] * 6)

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out", max_calls=99)

    assert result.calls <= MAX_CALLS
    assert len(llm.seen) <= MAX_CALLS


def test_transient_retries_consume_the_remaining_call_budget(tmp_path: Path, monkeypatch) -> None:
    waits: list[float] = []

    class AlwaysTransientLLM:
        def complete(self, messages, tools=None, *, on_text=None, stop=None) -> LLMReply:
            raise LLMError("temporary", retryable=True)

    monkeypatch.setattr("backend.generate.unit.time.sleep", waits.append)

    result = generate_unit(_request(tmp_path), llm=AlwaysTransientLLM(),
                           bundle=load_bundle(SKILL_DIR), fonts_css="", out_dir=tmp_path / "out")

    assert result.status is UnitStatus.FAILED
    assert result.calls == 3
    assert len(waits) == 2


def test_malformed_tool_input_becomes_a_tool_result(tmp_path: Path) -> None:
    tool_call = ToolCall("call_1", "read_reference", "not-json")
    llm = ScriptedLLM([LLMReply(text="", tool_calls=[tool_call]), LLMReply(text=GOOD)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.status is UnitStatus.OK
    assert result.requested_refs == []
    tool_message = [m for m in llm.seen[1] if m.get("role") == "tool"]
    assert tool_message and "malformed tool arguments" in tool_message[0]["content"]


def test_font_css_is_injected_before_the_artifact_is_published(tmp_path: Path) -> None:
    css = "@font-face{font-family:'Instrument Serif';src:url(data:font/woff2;base64,abc)}"
    llm = ScriptedLLM([LLMReply(text=GOOD)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css=css, out_dir=tmp_path / "out")

    assert result.status is UnitStatus.OK
    written = result.artifact_path.read_text(encoding="utf-8")
    assert css in written


def test_latex_in_the_reply_is_rendered_before_the_artifact_is_published(tmp_path: Path) -> None:
    candidate = GOOD.replace(
        "</body>", '<p>Heat \\(Q = mc\\Delta T\\).</p></body>', 1
    )
    llm = ScriptedLLM([LLMReply(text=candidate)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.status is UnitStatus.OK
    written = result.artifact_path.read_text(encoding="utf-8")
    assert "<math" in written
    assert "display=\"inline\"" in written
    assert "\\(" not in written


def test_embedded_css_data_urls_are_allowed(tmp_path: Path) -> None:
    css = (
        "@font-face{font-family:'Instrument Serif';"
        "src:url(data:font/woff2;base64,abc)}"
        ".icon{background-image:url(data:image/png;base64,abc)}"
    )
    llm = ScriptedLLM([LLMReply(text=GOOD)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css=css, out_dir=tmp_path / "out")

    assert result.status is UnitStatus.OK
    assert result.calls == 1
    written = result.artifact_path.read_text(encoding="utf-8")
    assert "data:font/woff2;base64,abc" in written
    assert "data:image/png;base64,abc" in written


@pytest.mark.parametrize(
    ("candidate", "finding_word"),
    [
        (
            '<img srcset="data:image/png;base64,abc 1x, assets/page.png 2x" alt="page">',
            "relative",
        ),
        (
            '<img srcset="data:image/png;base64,abc 1x, https://example.com/page.png 2x" alt="page">',
            "remote",
        ),
    ],
)
def test_mixed_srcset_candidates_are_sent_to_repair(
    tmp_path: Path, candidate: str, finding_word: str
) -> None:
    invalid = GOOD.replace("<body>", f"<body>{candidate}", 1)
    llm = ScriptedLLM([LLMReply(text=invalid), LLMReply(text=GOOD)])

    result = generate_unit(
        _request(tmp_path),
        llm=llm,
        bundle=load_bundle(SKILL_DIR),
        fonts_css="",
        out_dir=tmp_path / "out",
    )

    assert result.calls == 2
    assert result.status is UnitStatus.OK
    assert finding_word in llm.seen[1][-1]["content"].lower()


@pytest.mark.parametrize(
    ("kind", "candidate_builder"),
    [
        (
            "relative image source",
            lambda html: html.replace(
                "<body>", '<body><img src="assets/page.png" alt="page">', 1
            ),
        ),
        (
            "relative stylesheet href",
            lambda html: html.replace(
                "</head>", '<link rel="stylesheet" href="styles/guide.css"></head>', 1
            ),
        ),
        (
            "relative CSS URL",
            lambda html: html.replace(
                "<style>", "<style>.hero{background-image:url('assets/hero.png')}\n", 1
            ),
        ),
    ],
)
def test_relative_asset_references_are_sent_to_repair(
    tmp_path: Path, kind: str, candidate_builder
) -> None:
    candidate = candidate_builder(GOOD)
    llm = ScriptedLLM([LLMReply(text=candidate), LLMReply(text=GOOD)])

    result = generate_unit(
        _request(tmp_path),
        llm=llm,
        bundle=load_bundle(SKILL_DIR),
        fonts_css="",
        out_dir=tmp_path / "out",
    )

    assert result.calls == 2, kind
    assert result.status is UnitStatus.OK
    assert "relative" in llm.seen[1][-1]["content"].lower()


def test_checker_passing_remote_css_is_repaired(tmp_path: Path) -> None:
    css = (
        "@import url('https://example.com/theme.css');"
        ".hero{background-image:url('https://example.com/background.png')}"
    )
    candidate = GOOD.replace("<style>", f"<style>{css}", 1)
    candidate_path = tmp_path / "remote-css.html"
    candidate_path.write_text(candidate, encoding="utf-8")
    assert run_self_check(candidate_path, SKILL_DIR).ok is True

    llm = ScriptedLLM([LLMReply(text=candidate), LLMReply(text=GOOD)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.calls == 2
    assert result.status is UnitStatus.OK
    assert "remote" in llm.seen[1][-1]["content"].lower()


def test_checker_passing_executable_css_urls_are_repaired(tmp_path: Path) -> None:
    css = (
        ".bad{background-image:url(javascript:alert(1))}"
        ".also-bad{background-image:url('vbscript:msgbox(1)')}"
    )
    candidate = GOOD.replace("<style>", f"<style>{css}", 1)
    candidate_path = tmp_path / "executable-css.html"
    candidate_path.write_text(candidate, encoding="utf-8")
    assert run_self_check(candidate_path, SKILL_DIR).ok is True

    llm = ScriptedLLM([LLMReply(text=candidate), LLMReply(text=GOOD)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.calls == 2
    assert result.status is UnitStatus.OK
    repair_prompt = llm.seen[1][-1]["content"].lower()
    assert "executable" in repair_prompt


def test_checker_passing_quoted_css_comment_marker_does_not_hide_remote_url(
    tmp_path: Path,
) -> None:
    css = (
        '.label::before{content:"/*";}'
        '.hero{background-image:url("https://example.com/background.png");}'
        '/*"*/'
    )
    candidate = GOOD.replace("<style>", f"<style>{css}", 1)
    candidate_path = tmp_path / "quoted-comment-css.html"
    candidate_path.write_text(candidate, encoding="utf-8")
    assert run_self_check(candidate_path, SKILL_DIR).ok is True

    llm = ScriptedLLM([LLMReply(text=candidate), LLMReply(text=GOOD)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.calls == 2
    assert result.status is UnitStatus.OK
    assert "remote" in llm.seen[1][-1]["content"].lower()


def test_checker_passing_escaped_remote_css_url_is_repaired(tmp_path: Path) -> None:
    css = r".hero{background-image:url(\68\74\74\70://example.com/background.png)}"
    candidate = GOOD.replace("<style>", f"<style>{css}", 1)
    candidate_path = tmp_path / "escaped-remote-css.html"
    candidate_path.write_text(candidate, encoding="utf-8")
    assert run_self_check(candidate_path, SKILL_DIR).ok is True

    llm = ScriptedLLM([LLMReply(text=candidate), LLMReply(text=GOOD)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.calls == 2
    assert result.status is UnitStatus.OK
    assert "remote" in llm.seen[1][-1]["content"].lower()


def test_checker_passing_escaped_executable_css_url_is_repaired(tmp_path: Path) -> None:
    css = r".bad{background-image:url(\6a avascript:alert(1))}"
    candidate = GOOD.replace("<style>", f"<style>{css}", 1)
    candidate_path = tmp_path / "escaped-executable-css.html"
    candidate_path.write_text(candidate, encoding="utf-8")
    assert run_self_check(candidate_path, SKILL_DIR).ok is True

    llm = ScriptedLLM([LLMReply(text=candidate), LLMReply(text=GOOD)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.calls == 2
    assert result.status is UnitStatus.OK
    assert "executable" in llm.seen[1][-1]["content"].lower()


def test_executable_tag_urls_are_repaired_even_if_checker_approves(tmp_path: Path) -> None:
    candidate = GOOD.replace(
        "<body>",
        '<body><a href="javascript:alert(1)">bad</a>'
        '<a href="vbscript:msgbox(1)">also bad</a>',
        1,
    )
    llm = ScriptedLLM([LLMReply(text=candidate), LLMReply(text=GOOD)])

    result = generate_unit(
        _request(tmp_path),
        llm=llm,
        bundle=load_bundle(SKILL_DIR),
        fonts_css="",
        out_dir=tmp_path / "out",
        checker=lambda path, skill_dir: CheckResult(True, []),
    )

    assert result.calls == 2
    assert result.status is UnitStatus.OK
    repair_prompt = llm.seen[1][-1]["content"].lower()
    assert "executable" in repair_prompt


def test_canonical_motion_controller_is_allowed(tmp_path: Path) -> None:
    candidate = tmp_path / "motion.html"
    candidate.write_text(MOTION, encoding="utf-8")
    assert run_self_check(candidate, SKILL_DIR).ok is True

    llm = ScriptedLLM([LLMReply(text=MOTION)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.calls == 1
    assert result.status is UnitStatus.OK


def test_safe_guide_controller_is_allowed(tmp_path: Path) -> None:
    candidate = GOOD.replace(
        "</body>",
        "<script data-guide-controls>"
        "document.querySelector('body').dataset.ready = 'true';"
        "</script></body>",
        1,
    )
    candidate_path = tmp_path / "safe-guide.html"
    candidate_path.write_text(candidate, encoding="utf-8")
    check = run_self_check(candidate_path, SKILL_DIR)
    assert check.ok, check.findings
    llm = ScriptedLLM([LLMReply(text=candidate)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.calls == 1
    assert result.status is UnitStatus.OK


def test_unsafe_guide_controller_is_repaired(tmp_path: Path) -> None:
    candidate = GOOD.replace(
        "</body>",
        "<script data-guide-controls>fetch('https://example.com');</script></body>",
        1,
    )
    llm = ScriptedLLM([LLMReply(text=candidate), LLMReply(text=GOOD)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.calls == 2
    assert result.status is UnitStatus.OK
    repair_prompt = llm.seen[1][-1]["content"].lower()
    assert "network" in repair_prompt


def test_preamble_and_markdown_fence_are_stripped_without_a_repair_call(
    tmp_path: Path,
) -> None:
    candidate = (
        "Here is the completed lesson:\n"
        f"```html\n{GOOD.strip()}\n```\n"
        "Hope this helps."
    )
    llm = ScriptedLLM([LLMReply(text=candidate)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.calls == 1
    assert len(llm.seen) == 1
    assert result.status is UnitStatus.OK
    written = result.artifact_path.read_text(encoding="utf-8")
    assert written == GOOD.strip() + "\n"
    assert "Here is the completed lesson" not in written
    assert "Hope this helps" not in written
    assert written.startswith("<!DOCTYPE html>")


def test_leading_whitespace_before_the_doctype_survives(tmp_path: Path) -> None:
    candidate = f"\n\n  {GOOD.strip()}\n\n"
    llm = ScriptedLLM([LLMReply(text=candidate)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.calls == 1
    assert result.status is UnitStatus.OK
    assert result.artifact_path.read_text(encoding="utf-8") == GOOD.strip() + "\n"


def test_fragment_without_a_document_is_still_repaired(tmp_path: Path) -> None:
    candidate = "Here is the lesson:\n<div><h1>Cell Division</h1></div>"
    llm = ScriptedLLM([LLMReply(text=candidate), LLMReply(text=GOOD)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.calls == 2
    assert result.status is UnitStatus.OK
    assert "outside" in llm.seen[1][-1]["content"].lower()


def test_two_concatenated_documents_are_still_refused(tmp_path: Path) -> None:
    candidate = f"{GOOD.strip()}\n{GOOD.strip()}"
    llm = ScriptedLLM([LLMReply(text=candidate), LLMReply(text=GOOD)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.calls == 2
    assert result.status is UnitStatus.OK
    assert "one complete <html> document" in llm.seen[1][-1]["content"]


def test_status_callback_reports_verification_and_repair(tmp_path: Path) -> None:
    events: list[UnitStatus] = []
    llm = ScriptedLLM([LLMReply(text=BROKEN), LLMReply(text=GOOD)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out",
                           status_callback=events.append)

    assert result.status is UnitStatus.OK
    assert events == [UnitStatus.VERIFYING, UnitStatus.REPAIRING, UnitStatus.VERIFYING]


def test_empty_completion_is_not_published_as_an_artifact(tmp_path: Path) -> None:
    llm = ScriptedLLM([LLMReply(text=""), LLMReply(text="   ")])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.status is UnitStatus.NEEDS_ATTENTION
    assert result.artifact_path is None
    assert not (tmp_path / "out" / "units" / "unit-1" / "artifact.html").exists()
    findings = "; ".join(result.findings)
    assert "no content" in findings.lower()
    # The empty file must not be blamed on the output policy.
    assert "one complete <html> document" not in findings
    assert "accessible (non-aria-hidden) SVG" not in findings


def test_empty_completion_is_retried_with_an_explanation(tmp_path: Path) -> None:
    llm = ScriptedLLM([LLMReply(text=""), LLMReply(text=GOOD)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.status is UnitStatus.OK
    assert result.calls == 2
    assert result.artifact_path.is_file()
    assert "no content" in llm.seen[1][-1]["content"].lower()


def test_reasoning_starved_reply_names_the_output_budget_as_the_cause(tmp_path: Path) -> None:
    starved = LLMReply(text="", finish_reason="length", completion_tokens=32768,
                       reasoning_tokens=32768)
    llm = ScriptedLLM([starved, starved])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.status is UnitStatus.NEEDS_ATTENTION
    assert result.artifact_path is None
    finding = result.findings[0].lower()
    assert "output budget" in finding
    assert "reasoning" in finding
    assert "32768" in finding
    assert "one complete <html> document" not in "; ".join(result.findings)


def test_truncated_document_is_reported_as_truncation(tmp_path: Path) -> None:
    truncated = GOOD[: GOOD.index("</body>")]
    llm = ScriptedLLM([
        LLMReply(text=truncated, finish_reason="length", completion_tokens=131072,
                 reasoning_tokens=46000),
        LLMReply(text=GOOD),
    ])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.calls == 2
    assert result.status is UnitStatus.OK
    repair_prompt = llm.seen[1][-1]["content"]
    assert "output budget mid-document" in repair_prompt
    assert "incomplete" in repair_prompt
    assert "46000" in repair_prompt


class ProgressRecorder:
    """Captures the activity, attempt, and counts shown to the screen."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, int, int]] = []
        self.reporter = ProgressReporter(on_change=self.record)

    def record(self) -> None:
        state = self.reporter.snapshot()
        self.seen.append((state.activity, state.attempt, state.lines))

    @property
    def activities(self) -> list[str]:
        return [activity for activity, _attempt, _lines in self.seen]

    def collapsed_activities(self) -> list[str]:
        collapsed: list[str] = []
        for activity in self.activities:
            if not collapsed or collapsed[-1] != activity:
                collapsed.append(activity)
        return collapsed


def test_progress_reports_real_activity_and_labelled_attempts(tmp_path: Path) -> None:
    reference_turn = LLMReply(
        text="",
        tool_calls=[ToolCall("call_1", "read_reference", '{"name":"type-process.md"}')],
    )
    llm = ScriptedLLM([reference_turn, LLMReply(text=BROKEN), LLMReply(text=GOOD)])
    recorder = ProgressRecorder()

    result = generate_unit(
        _request(tmp_path),
        llm=llm,
        bundle=load_bundle(SKILL_DIR),
        fonts_css="",
        out_dir=tmp_path / "out",
        progress=recorder.reporter,
    )

    assert result.status is UnitStatus.OK
    assert recorder.collapsed_activities() == [
        "preparing",
        "waiting",
        "references",
        "waiting",
        "writing",
        "checking",
        "correcting",
        "waiting",
        "writing",
        "checking",
    ]
    attempts = [attempt for _activity, attempt, _lines in recorder.seen]
    assert attempts == sorted(attempts)
    assert max(attempts) == 3
    reference_lines = [
        lines for activity, _attempt, lines in recorder.seen if activity == "references"
    ]
    assert set(reference_lines) == {0}
    assert recorder.seen[-1] == ("checking", 3, count_document_lines(GOOD))
    assert len(llm.seen) == 3
    assert result.calls == 3


def test_a_tool_only_turn_leaves_no_document_count_behind(tmp_path: Path) -> None:
    reference_turn = LLMReply(
        text="",
        tool_calls=[ToolCall("call_1", "read_reference", '{"name":"type-process.md"}')],
    )
    llm = ScriptedLLM([reference_turn, LLMReply(text=GOOD)])
    recorder = ProgressRecorder()

    result = generate_unit(
        _request(tmp_path),
        llm=llm,
        bundle=load_bundle(SKILL_DIR),
        fonts_css="",
        out_dir=tmp_path / "out",
        progress=recorder.reporter,
    )

    assert result.status is UnitStatus.OK
    assert "references" in recorder.collapsed_activities()
    writing_lines = [
        lines for activity, _attempt, lines in recorder.seen if activity == "writing"
    ]
    assert writing_lines == [count_document_lines(GOOD)]


def test_a_retried_network_failure_starts_a_labelled_new_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("backend.generate.unit.time.sleep", lambda _seconds: None)

    class FlakyLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages, tools=None, *, on_text=None, stop=None) -> LLMReply:
            self.calls += 1
            if self.calls == 1:
                raise LLMError("temporary network failure", retryable=True)
            if on_text is not None:
                on_text(GOOD)
            return LLMReply(text=GOOD)

    llm = FlakyLLM()
    recorder = ProgressRecorder()

    result = generate_unit(
        _request(tmp_path),
        llm=llm,
        bundle=load_bundle(SKILL_DIR),
        fonts_css="",
        out_dir=tmp_path / "out",
        progress=recorder.reporter,
    )

    assert result.status is UnitStatus.OK
    assert recorder.collapsed_activities() == [
        "preparing",
        "waiting",
        "retry",
        "waiting",
        "writing",
        "checking",
    ]
    assert recorder.seen[-1] == ("checking", 2, count_document_lines(GOOD))


def test_a_stopped_request_neither_calls_the_model_nor_publishes(tmp_path: Path) -> None:
    stop = Event()
    stop.set()
    llm = ScriptedLLM([LLMReply(text=GOOD)])

    result = generate_unit(
        _request(tmp_path),
        llm=llm,
        bundle=load_bundle(SKILL_DIR),
        fonts_css="",
        out_dir=tmp_path / "out",
        stop=stop,
    )

    assert result.status is UnitStatus.FAILED
    assert result.artifact_path is None
    assert "stopped" in "; ".join(result.findings).lower()
    assert llm.seen == []
    assert not (tmp_path / "out" / "units" / "unit-1" / "artifact.html").exists()


def test_a_stop_while_a_candidate_is_unverified_discards_it(tmp_path: Path) -> None:
    stop = Event()

    class StoppingLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages, tools=None, *, on_text=None, stop=None) -> LLMReply:
            self.calls += 1
            if on_text is not None:
                on_text(BROKEN)
            stop_event.set()
            return LLMReply(text=BROKEN)

    stop_event = stop
    llm = StoppingLLM()

    result = generate_unit(
        _request(tmp_path),
        llm=llm,
        bundle=load_bundle(SKILL_DIR),
        fonts_css="",
        out_dir=tmp_path / "out",
        stop=stop_event,
    )

    assert llm.calls == 1
    assert result.status is UnitStatus.FAILED
    assert result.artifact_path is None
    assert "stopped" in "; ".join(result.findings).lower()
    assert not (tmp_path / "out" / "units" / "unit-1" / "artifact.html").exists()


def test_reference_lookups_do_not_consume_the_generation_attempts(tmp_path: Path) -> None:
    reads = [
        LLMReply(
            text="",
            tool_calls=[
                ToolCall(f"call_{index}", "read_reference", '{"name":"type-process.md"}')
            ],
        )
        for index in range(4)
    ]
    llm = ScriptedLLM([*reads, LLMReply(text=GOOD)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.status is UnitStatus.OK
    assert result.calls == 5
    assert len(result.requested_refs) == 4


DRAFT = '<html><head><title>Unit 12 Waves</title></head><body><p>Waves</p></body></html>'


def _patch_call(find: str, replace: str) -> ToolCall:
    return ToolCall(
        "call_patch", PATCH_TOOL, json.dumps({"edits": [{"find": find, "replace": replace}]})
    )


def _verdicts(*results: CheckResult):
    """A checker that returns each verdict in turn."""
    remaining = list(results)
    return lambda path, skill_dir: remaining.pop(0)


def test_apply_edits_requires_each_find_to_match_exactly_once() -> None:
    twice = "<p>one</p><p>one</p>"
    ambiguous, note = _apply_edits(
        twice, {"edits": [{"find": "<p>one</p>", "replace": "<p>two</p>"}]}
    )
    assert ambiguous is None
    assert "matched 2 times" in note

    missing, note = _apply_edits(
        twice, {"edits": [{"find": "<p>absent</p>", "replace": "x"}]}
    )
    assert missing is None
    assert "matched no text" in note

    assert _apply_edits(twice, {"edits": []})[0] is None
    assert _apply_edits(twice, {"edits": ["not an object"]})[0] is None
    assert _apply_edits(twice, "nonsense")[0] is None


def test_edits_apply_in_order_so_one_can_build_on_another() -> None:
    patched, note = _apply_edits(
        "<p>a</p>",
        {
            "edits": [
                {"find": "<p>a</p>", "replace": "<p>b</p>"},
                {"find": "<p>b</p>", "replace": "<p>c</p>"},
            ]
        },
    )

    assert patched == "<p>c</p>"
    assert "2 edit" in note


def test_a_patch_is_refused_without_a_document_or_once_the_budget_is_spent() -> None:
    call = _patch_call("a", "b")

    patched, note = _patched_document([call], None, 0)
    assert patched is None
    assert "no document to patch" in note

    patched, note = _patched_document([call], "a", MAX_PATCHES)
    assert patched is None
    assert "patch limit" in note

    patched, _ = _patched_document([call], "a", MAX_PATCHES - 1)
    assert patched == "b"

    # A turn that made no patch request is not a refusal, just nothing to do.
    assert _patched_document([], "a", 0) == (None, "")


def test_a_patch_repairs_the_document_without_rewriting_it(tmp_path: Path) -> None:
    patch = _patch_call("<p>Waves</p>", "<p>Waves and energy</p>")
    llm = ScriptedLLM([LLMReply(text=DRAFT), LLMReply(text="", tool_calls=[patch])])

    result = generate_unit(
        _request(tmp_path),
        llm=llm,
        bundle=load_bundle(SKILL_DIR),
        fonts_css="",
        out_dir=tmp_path / "out",
        checker=_verdicts(
            CheckResult(False, ["figure 1 has no accessible name"]), CheckResult(True, [])
        ),
    )

    assert result.status is UnitStatus.OK
    # One repair call carrying an edit, not one carrying the whole document again.
    assert result.calls == 2
    written = result.artifact_path.read_text(encoding="utf-8")
    assert "Waves and energy" in written
    assert "<p>Waves</p>" not in written


def test_a_forbidden_script_call_is_patched_out_under_the_real_gates(tmp_path: Path) -> None:
    # The shape production actually failed on: the output policy and the shipped
    # checker both reject a Function constructor, and both are real here.
    offending = "<script data-guide-controls>const f = new Function('x', 'y');</script>"
    candidate = GOOD.replace("</body>", f"{offending}</body>", 1)
    assert candidate != GOOD
    replacement = "<script data-guide-controls>function f(x, y) { return x; }</script>"
    llm = ScriptedLLM(
        [
            LLMReply(text=candidate),
            LLMReply(text="", tool_calls=[_patch_call(offending, replacement)]),
        ]
    )

    result = generate_unit(
        _request(tmp_path),
        llm=llm,
        bundle=load_bundle(SKILL_DIR),
        fonts_css="",
        out_dir=tmp_path / "out",
    )

    assert result.status is UnitStatus.OK
    assert result.calls == 2
    assert result.findings == []
    written = result.artifact_path.read_text(encoding="utf-8")
    assert "new Function" not in written
    assert run_self_check(result.artifact_path, SKILL_DIR).ok is True


def test_the_patch_tool_is_offered_only_once_a_document_exists(tmp_path: Path) -> None:
    llm = ScriptedLLM([LLMReply(text=BROKEN), LLMReply(text=GOOD)])

    generate_unit(
        _request(tmp_path),
        llm=llm,
        bundle=load_bundle(SKILL_DIR),
        fonts_css="",
        out_dir=tmp_path / "out",
    )

    def offered(index: int) -> set[str]:
        return {tool["function"]["name"] for tool in llm.tools[index] or []}

    # Nothing has been built on the first turn, so there is nothing to edit.
    assert PATCH_TOOL not in offered(0)
    # The repair turn has a rejected document, so editing it is on the table.
    assert PATCH_TOOL in offered(1)


def test_an_ambiguous_edit_is_refused_instead_of_guessing(tmp_path: Path) -> None:
    twice = (
        '<html><head><title>Unit 12</title></head>'
        "<body><p>Waves</p><p>Waves</p></body></html>"
    )
    llm = ScriptedLLM(
        [
            LLMReply(text=twice),
            LLMReply(text="", tool_calls=[_patch_call("<p>Waves</p>", "<p>x</p>")]),
            LLMReply(text=DRAFT),
        ]
    )

    result = generate_unit(
        _request(tmp_path),
        llm=llm,
        bundle=load_bundle(SKILL_DIR),
        fonts_css="",
        out_dir=tmp_path / "out",
        checker=_verdicts(
            CheckResult(False, ["figure 1 has no accessible name"]), CheckResult(True, [])
        ),
    )

    assert result.status is UnitStatus.OK
    refusals = [m for m in llm.seen[2] if m.get("role") == "tool"]
    assert any("matched 2 times" in m["content"] for m in refusals)
    # The ambiguous edit touched nothing, so the guessed replacement is absent.
    assert "<p>x</p>" not in result.artifact_path.read_text(encoding="utf-8")


def test_a_patch_that_still_fails_reports_the_new_findings(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            LLMReply(text=DRAFT),
            LLMReply(
                text="",
                tool_calls=[_patch_call("<p>Waves</p>", "<p>Waves and energy</p>")],
            ),
            LLMReply(text=DRAFT),
        ]
    )

    result = generate_unit(
        _request(tmp_path),
        llm=llm,
        bundle=load_bundle(SKILL_DIR),
        fonts_css="",
        out_dir=tmp_path / "out",
        checker=_verdicts(
            CheckResult(False, ["first problem"]),
            CheckResult(False, ["second problem"]),
            CheckResult(True, []),
        ),
    )

    assert result.status is UnitStatus.OK
    feedback = [m for m in llm.seen[2] if m.get("role") == "tool"]
    assert any("still fails verification" in m["content"] for m in feedback)
    assert any("second problem" in m["content"] for m in feedback)


def _traced_records(trace: Trace, event: str) -> list[dict]:
    records = [
        json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()
    ]
    return [record for record in records if record["event"] == event]


def test_logged_findings_keeps_short_text_and_bounds_long_text() -> None:
    assert logged_findings(["svg is missing role=img"]) == ["svg is missing role=img"]

    long_finding = "x" * (FINDING_LOG_LIMIT + 50)
    assert logged_findings([long_finding]) == ["x" * (FINDING_LOG_LIMIT - 3) + "..."]

    many = logged_findings([f"finding {index}" for index in range(FINDING_LOG_MAX + 3)])
    assert len(many) == FINDING_LOG_MAX + 1
    assert many[-1] == "... and 3 more"


def test_a_repaired_artifact_still_records_the_checker_finding_text(tmp_path: Path) -> None:
    llm = ScriptedLLM([LLMReply(text=BROKEN), LLMReply(text=GOOD)])
    trace = trace_for(tmp_path / "jobs", "unit-repaired")
    verdicts = [CheckResult(False, ["svg is missing role=img"]), CheckResult(True, [])]

    def checker(path: Path, skill_dir: Path) -> CheckResult:
        return verdicts.pop(0)

    with use_trace(trace):
        result = generate_unit(
            _request(tmp_path),
            llm=llm,
            bundle=load_bundle(SKILL_DIR),
            fonts_css="",
            out_dir=tmp_path / "out",
            checker=checker,
        )

    assert result.status is UnitStatus.OK
    # A run that repairs itself publishes with an empty finding list, so the trace
    # is the only place the rejected artifact's messages survive.
    assert result.findings == []

    checking = _traced_records(trace, "artifact.checker")[0]
    assert checking["messages"] == ["svg is missing role=img"]
    rejected = _traced_records(trace, "artifact.rejected")[0]
    assert rejected["findings"] == 1
    assert "svg is missing role=img" in rejected["messages"]


def test_policy_findings_are_recorded_as_text(tmp_path: Path) -> None:
    candidate = GOOD.replace("<body>", '<body><img src="assets/page.png" alt="page">', 1)
    llm = ScriptedLLM([LLMReply(text=candidate), LLMReply(text=GOOD)])
    trace = trace_for(tmp_path / "jobs", "unit-policy")

    with use_trace(trace):
        result = generate_unit(
            _request(tmp_path),
            llm=llm,
            bundle=load_bundle(SKILL_DIR),
            fonts_css="",
            out_dir=tmp_path / "out",
            checker=lambda path, skill_dir: CheckResult(True, []),
        )

    assert result.status is UnitStatus.OK
    policy = _traced_records(trace, "artifact.policy")[0]
    assert policy["messages"]
    assert any("relative" in message for message in policy["messages"])
