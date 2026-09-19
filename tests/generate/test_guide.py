from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from backend.generate.guide import GuideRequest, generate_guide
from backend.generate.revision import RevisionRequest, revise_guide
from backend.ingest.source import store_source
from backend.llm.client import LLMReply
from backend.skill.bundle import load_bundle
from backend.verify.self_check import CheckResult

SKILL_DIR = Path("diagram-design")
GOOD = (SKILL_DIR / "assets" / "template.html").read_text(encoding="utf-8")


class ScriptedLLM:
    def __init__(self, replies: list[LLMReply]) -> None:
        self._replies = list(replies)
        self.seen: list[list[dict]] = []

    def complete(self, messages, tools=None) -> LLMReply:
        self.seen.append(list(messages))
        return self._replies.pop(0)


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
