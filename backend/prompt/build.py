from __future__ import annotations

from collections.abc import Mapping, Sequence
from html.parser import HTMLParser

OUTPUT_POLICY = """\
Hard requirements:
- Return the raw HTML document only. No markdown fences, no commentary.
- No <script> tags and no motion markup.
- No remote assets: fonts are embedded, and nothing loads from the network.
- The diagram <svg> needs role="img", with <title> as its first child, non-empty
  <title> and <desc>, and aria-labelledby naming those two ids in order.
"""


def build_initial_message(
    source_kind: str, item_count: int, selection: Mapping[str, object]
) -> str:
    source_description = _source_description(source_kind, item_count, selection)
    return (
        f"Source: {source_description}\n\n"
        "Create ONE self-contained HTML study guide from the attached source material. "
        "Use the fixed Diagram-design system in your system context to decide the guide's "
        "structure, diagrams, typography, and visual hierarchy. Do not ask for or rely on "
        "any additional topic or writing request.\n\n"
        "Include one concise, human-readable <title> in the HTML <head>; that title will be "
        "used as the guide name.\n\n"
        + OUTPUT_POLICY
    )


def build_revision_message(selected_text: str, instruction: str, current_html: str) -> str:
    return (
        "Revise the current study guide using the selected text and the requested change. "
        "Return the full revised HTML document, not a fragment or a diff.\n\n"
        "Selected text:\n<selected-text>\n"
        f"{selected_text}\n"
        "</selected-text>\n\n"
        "Requested change:\n<revision-instruction>\n"
        f"{instruction}\n"
        "</revision-instruction>\n\n"
        "Current complete HTML document:\n<current-html>\n"
        f"{current_html}\n"
        "</current-html>\n\n"
        "Return only the complete revised HTML document. Preserve the offline, accessible SVG, "
        "and no-motion output policy below.\n\n"
        + OUTPUT_POLICY
    )


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._inside_head = False
        self._inside_title = False
        self._seen_title = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag == "head":
            self._inside_head = True
        elif normalized_tag == "title" and self._inside_head and not self._seen_title:
            self._seen_title = True
            self._inside_title = True

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag == "title" and self._inside_title:
            self._inside_title = False
        elif normalized_tag == "head":
            self._inside_head = False

    def handle_data(self, data: str) -> None:
        if self._inside_title:
            self.parts.append(data)


def extract_guide_title(html: str) -> str:
    parser = _TitleParser()
    parser.feed(html)
    parser.close()
    title = " ".join("".join(parser.parts).split())
    return title or "Untitled guide"


def _source_description(
    source_kind: str, item_count: int, selection: Mapping[str, object]
) -> str:
    if item_count < 0:
        raise ValueError("item_count cannot be negative")
    if source_kind == "pdf":
        mode = selection.get("mode")
        if mode == "all":
            return f"the complete PDF ({item_count} page(s), in order)"
        if mode == "custom":
            start = selection.get("start")
            end = selection.get("end")
            if not isinstance(start, int) or isinstance(start, bool) or not isinstance(end, int) or isinstance(end, bool):
                raise ValueError("custom PDF selection requires integer start and end")
            return f"PDF pages {start}–{end} inclusive ({item_count} page(s), in order)"
        raise ValueError("PDF sources require an all or custom selection")
    if source_kind == "images" and selection.get("mode") == "images":
        return f"{item_count} original image(s), ordered as supplied"
    raise ValueError("image sources require an images selection")

INSTRUCTION = """\
Subject: {label}
Attached: {count} page image(s) of the source material, in order.
Page count: {count} pages.

Read the pages, then produce ONE self-contained HTML study artifact for this unit,
following the design system in your instructions.

Hard requirements:
- Return the raw HTML document only. No markdown fences, no commentary.
- No <script> tags and no motion markup.
- No remote assets: fonts are embedded, and nothing loads from the network.
- The diagram <svg> needs role="img", with <title> as its first child, non-empty
  <title> and <desc>, and aria-labelledby naming those two ids in order.
"""


def build_unit_message(unit_label: str, page_numbers: Sequence[int]) -> str:
    return INSTRUCTION.format(label=unit_label, count=len(page_numbers))
