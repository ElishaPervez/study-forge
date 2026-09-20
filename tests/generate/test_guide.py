from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from backend.generate.guide import GuideRequest, generate_guide
from backend.generate.progress import ProgressReporter
from backend.generate.revision import RevisionRequest, revise_guide
from backend.ingest.source import store_source
from backend.llm.client import LLMReply, ToolCall
from backend.skill.bundle import load_bundle
from backend.verify.self_check import CheckResult

SKILL_DIR = Path("diagram-design")
GOOD = (SKILL_DIR / "assets" / "template.html").read_text(encoding="utf-8")


class ScriptedLLM:
    def __init__(self, replies: list[LLMReply]) -> None:
        self._replies = list(replies)
        self.seen: list[list[dict]] = []

    def complete(self, messages, tools=None, *, on_text=None, stop=None) -> LLMReply:
        self.seen.append(list(messages))
        reply = self._replies.pop(0)
        if on_text is not None:
            on_text(reply.text)
        return reply


def _write_image(path: Path) -> None:
    Image.new("RGB", (8, 8), (25, 75, 125)).save(path, format="PNG")


def test_generate_guide_uses_source_selection_and_model_title(tmp_path: Path) -> None:
    image = tmp_path / "lesson.png"
    _write_image(image)
    source = store_source(tmp_path / "jobs", [image], "images")
    llm = ScriptedLLM([LLMReply(text=GOOD.replace("<title>Diagram</title>", "<title>Cell Division</title>"))])

    result = generate_guide(
        GuideRequest("guide-1", source, {"mode": "images"}),
        source_root=tmp_path / "jobs",
        llm=llm,
        bundle=load_bundle(SKILL_DIR),
        fonts_css="",
        out_dir=tmp_path / "out",
    )

    assert result.name == "Cell Division"
    content = llm.seen[0][1]["content"]
    assert content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "original image bytes" not in content[-1]["text"]
    assert "instruction" not in content[-1]["text"].lower()


def test_generate_guide_accepts_a_selected_pdf_range(tmp_path: Path) -> None:
    pdf = tmp_path / "source.pdf"
    document = pymupdf.open()
    for number in range(3):
        page = document.new_page()
        page.insert_text((72, 120), f"Page {number + 1}")
    document.save(pdf)
    document.close()
    source = store_source(tmp_path / "jobs", [pdf], "pdf")
    llm = ScriptedLLM([LLMReply(text=GOOD)])

    result = generate_guide(
        GuideRequest("guide-2", source, {"mode": "custom", "start": 2, "end": 3}),
        source_root=tmp_path / "jobs",
        llm=llm,
        bundle=load_bundle(SKILL_DIR),
        fonts_css="",
        out_dir=tmp_path / "out",
    )

    assert result.status.value == "ok"
    message = llm.seen[0][1]["content"][-1]["text"]
    assert "2–3" in message
    assert [part["image_url"]["url"][: len("data:image/jpeg;base64,")] for part in llm.seen[0][1]["content"][:-1]] == [
        "data:image/jpeg;base64,"
    ] * 2


def test_revise_guide_sends_selected_text_instruction_and_current_html(tmp_path: Path) -> None:
    image = tmp_path / "lesson.png"
    _write_image(image)
    source = store_source(tmp_path / "jobs", [image], "images")
    current_html = GOOD
    revised_html = GOOD.replace("<title>Diagram</title>", "<title>Revised lesson</title>")
    llm = ScriptedLLM([LLMReply(text=revised_html)])

    result = revise_guide(
        RevisionRequest(
            "guide-3",
            source,
            {"mode": "images"},
            "the selected passage",
            "Explain the distinction",
            "custom",
            current_html,
        ),
        source_root=tmp_path / "jobs",
        llm=llm,
        bundle=load_bundle(SKILL_DIR),
        fonts_css="",
        checker=lambda _path, _skill_dir: CheckResult(True, []),
    )

    assert result.html is not None
    assert "<title>Revised lesson</title>" in result.html
    message = llm.seen[0][1]["content"][-1]["text"]
    assert "the selected passage" in message
    assert "Explain the distinction" in message
    assert current_html in message


@pytest.mark.parametrize(
    ("bad_candidate", "finding_word"),
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
def test_revision_repair_prompt_reports_mixed_srcset_candidates(
    tmp_path: Path, bad_candidate: str, finding_word: str
) -> None:
    image = tmp_path / "lesson.png"
    _write_image(image)
    source = store_source(tmp_path / "jobs", [image], "images")
    invalid = GOOD.replace("<body>", f"<body>{bad_candidate}", 1)
    llm = ScriptedLLM([LLMReply(text=invalid), LLMReply(text=GOOD)])

    result = revise_guide(
        RevisionRequest(
            "guide-4",
            source,
            {"mode": "images"},
            "the selected passage",
            "Explain the distinction",
            "custom",
            GOOD,
        ),
        source_root=tmp_path / "jobs",
        llm=llm,
        bundle=load_bundle(SKILL_DIR),
        fonts_css="",
        checker=lambda _path, _skill_dir: CheckResult(True, []),
    )

    assert result.calls == 2
    assert result.html is not None
    assert finding_word in llm.seen[1][-1]["content"].lower()


def _revision_request(source, current_html: str = GOOD) -> RevisionRequest:
    return RevisionRequest(
        "guide-5",
        source,
        {"mode": "images"},
        "the selected passage",
        "Explain the distinction",
        "custom",
        current_html,
    )


def _revise(tmp_path: Path, llm) -> object:
    image = tmp_path / "lesson.png"
    _write_image(image)
    source = store_source(tmp_path / "jobs", [image], "images")
    return revise_guide(
        _revision_request(source),
        source_root=tmp_path / "jobs",
        llm=llm,
        bundle=load_bundle(SKILL_DIR),
        fonts_css="",
        checker=lambda _path, _skill_dir: CheckResult(True, []),
    )


def test_revision_without_content_names_the_real_cause(tmp_path: Path) -> None:
    starved = LLMReply(text="", finish_reason="length", completion_tokens=65536,
                       reasoning_tokens=65536)

    result = _revise(tmp_path, ScriptedLLM([starved, starved]))

    assert result.html is None
    finding = result.findings[0].lower()
    assert "output budget" in finding
    assert "reasoning" in finding
    assert "one complete <html> document" not in "; ".join(result.findings)


def test_revision_without_content_is_retried_with_an_explanation(tmp_path: Path) -> None:
    llm = ScriptedLLM([LLMReply(text=""), LLMReply(text=GOOD)])

    result = _revise(tmp_path, llm)

    assert result.html is not None
    assert result.calls == 2
    assert "no content" in llm.seen[1][-1]["content"].lower()


def test_revision_renders_latex_into_mathml(tmp_path: Path) -> None:
    revised = GOOD.replace("</body>", "<p>So \\(v = f\\lambda\\).</p></body>", 1)
    llm = ScriptedLLM([LLMReply(text=revised)])

    result = _revise(tmp_path, llm)

    assert result.html is not None
    assert "<math" in result.html
    assert "\\(" not in result.html


def test_revision_truncation_is_named_in_the_repair_prompt(tmp_path: Path) -> None:
    truncated = GOOD[: GOOD.index("</body>")]
    llm = ScriptedLLM([
        LLMReply(text=truncated, finish_reason="length", completion_tokens=131072),
        LLMReply(text=GOOD),
    ])

    result = _revise(tmp_path, llm)

    assert result.html is not None
    assert "output budget mid-document" in llm.seen[1][-1]["content"]


class ActivityRecorder:
    def __init__(self) -> None:
        self.activities: list[str] = []
        self.reporter = ProgressReporter(on_change=self.record)

    def record(self) -> None:
        activity = self.reporter.snapshot().activity
        if not self.activities or self.activities[-1] != activity:
            self.activities.append(activity)


def test_observing_progress_does_not_change_the_generated_guide(tmp_path: Path) -> None:
    image = tmp_path / "lesson.png"
    _write_image(image)
    source = store_source(tmp_path / "jobs", [image], "images")
    bundle = load_bundle(SKILL_DIR)
    request = GuideRequest("guide-6", source, {"mode": "images"})

    plain = generate_guide(
        request,
        source_root=tmp_path / "jobs",
        llm=ScriptedLLM([LLMReply(text=GOOD)]),
        bundle=bundle,
        fonts_css="",
        out_dir=tmp_path / "plain",
    )
    recorder = ActivityRecorder()
    measured = generate_guide(
        request,
        source_root=tmp_path / "jobs",
        llm=ScriptedLLM([LLMReply(text=GOOD)]),
        bundle=bundle,
        fonts_css="",
        out_dir=tmp_path / "measured",
        progress=recorder.reporter,
    )

    assert measured.status is plain.status
    assert measured.calls == plain.calls
    assert measured.name == plain.name
    assert measured.requested_refs == plain.requested_refs
    assert measured.artifact_path is not None and plain.artifact_path is not None
    assert measured.artifact_path.read_bytes() == plain.artifact_path.read_bytes()
    assert recorder.activities == ["preparing", "waiting", "writing", "checking"]


def test_revision_progress_reports_reference_work_and_correction(tmp_path: Path) -> None:
    reference_turn = LLMReply(
        text="",
        tool_calls=[ToolCall("call_1", "read_reference", '{"name":"type-process.md"}')],
    )
    invalid = GOOD.replace("<body>", '<body><img src="assets/page.png" alt="page">', 1)
    llm = ScriptedLLM([reference_turn, LLMReply(text=invalid), LLMReply(text=GOOD)])
    recorder = ActivityRecorder()

    result = _revise_with(
        tmp_path,
        llm,
        progress=recorder.reporter,
    )

    assert result.html is not None
    assert result.calls == 3
    assert recorder.activities == [
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


def _revise_with(tmp_path: Path, llm, *, progress) -> object:
    image = tmp_path / "lesson.png"
    _write_image(image)
    source = store_source(tmp_path / "jobs", [image], "images")
    return revise_guide(
        _revision_request(source),
        source_root=tmp_path / "jobs",
        llm=llm,
        bundle=load_bundle(SKILL_DIR),
        fonts_css="",
        checker=lambda _path, _skill_dir: CheckResult(True, []),
        progress=progress,
    )


def test_revision_reference_lookups_do_not_consume_the_attempts(tmp_path: Path) -> None:
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

    result = _revise(tmp_path, llm)

    assert result.html is not None
    assert result.calls == 5
    assert len(result.requested_refs) == 4
