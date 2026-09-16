from pathlib import Path

from backend.generate.unit import UnitRequest, UnitStatus, generate_unit
from backend.ingest.pdf import PageImage
from backend.llm.client import LLMError, LLMReply, ToolCall
from backend.skill.bundle import load_bundle
from backend.verify.self_check import run_self_check

SKILL_DIR = Path("diagram-design")
GOOD = (SKILL_DIR / "assets" / "template.html").read_text(encoding="utf-8")
BROKEN = '<html><body><svg viewBox="0 0 100 100"></svg></body></html>'


class ScriptedLLM:
    """Replays a fixed script and records the messages it was shown."""

    def __init__(self, replies: list[LLMReply]) -> None:
        self._replies = list(replies)
        self.seen: list[list[dict]] = []

    def complete(self, messages, tools=None) -> LLMReply:
        self.seen.append(list(messages))
        return self._replies.pop(0)


def _request(tmp_path: Path, numbers=(1, 2)) -> UnitRequest:
    pages = []
    for number in numbers:
        path = tmp_path / f"{number:04d}.jpg"
        path.write_bytes(b"\xff\xd8\xff\xe0x")
        pages.append(PageImage(number, path, 100, 140))
    return UnitRequest("unit-1", "Cell Division", list(numbers), pages)


def test_single_call_unit_that_passes_stops_at_one_call(tmp_path: Path) -> None:
    llm = ScriptedLLM([LLMReply(text=GOOD)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.status is UnitStatus.OK
    assert result.calls == 1
    written = result.artifact_path.read_text(encoding="utf-8")
    # read_text normalizes newlines, so autocrlf cannot make this flaky.
    assert written == GOOD.strip() + "\n"
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
    llm = ScriptedLLM([LLMReply(text=BROKEN), LLMReply(text=BROKEN), LLMReply(text=BROKEN)])

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.calls == 3
    assert result.status is UnitStatus.NEEDS_ATTENTION
    assert result.findings
    assert result.artifact_path.is_file()


def test_never_exceeds_the_call_budget(tmp_path: Path) -> None:
    tool_call = ToolCall("call_1", "read_reference", '{"name":"type-architecture.md"}')
    llm = ScriptedLLM([LLMReply(text="", tool_calls=[tool_call])] * 6)

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out")

    assert result.calls <= 3
    assert result.status is UnitStatus.NEEDS_ATTENTION


def test_caller_limit_is_clamped_to_the_global_budget(tmp_path: Path) -> None:
    tool_call = ToolCall("call_1", "read_reference", '{"name":"type-process.md"}')
    llm = ScriptedLLM([LLMReply(text="", tool_calls=[tool_call])] * 6)

    result = generate_unit(_request(tmp_path), llm=llm, bundle=load_bundle(SKILL_DIR),
                           fonts_css="", out_dir=tmp_path / "out", max_calls=99)

    assert result.calls == 3
    assert len(llm.seen) == 3


def test_transient_retries_consume_the_remaining_call_budget(tmp_path: Path, monkeypatch) -> None:
    waits: list[float] = []

    class AlwaysTransientLLM:
        def complete(self, messages, tools=None) -> LLMReply:
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
