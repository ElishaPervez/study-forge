from __future__ import annotations

from collections.abc import Mapping, Sequence
from html.parser import HTMLParser

# Raw strings: the LaTeX delimiters and command names below must reach the model
# exactly as written, and "\f"/"\t" are escape sequences in a normal string.
OUTPUT_POLICY = r"""Hard requirements:
- Return the raw HTML document only. No markdown fences, no commentary.
- Return a complete student study guide, not a diagram gallery or a prose dump.
- Follow the study-guide architecture: orientation, conceptual spine, numbered sections,
  meaningful visuals, exam/application layer, retrieval practice, and closing recall.
  Do not force a section when the source does not support it, but make the guide feel like
  a coherent learning journey rather than unrelated cards.
- Read the `study-guide.md` reference before writing. Use the matching diagram reference
  for every substantial visual and `animation.md` when a sequence interaction is used.
- Write every equation as LaTeX: inline math between `\(` and `\)`, display math between
  `\[` and `\]`. The build step converts those delimiters into native MathML, so never
  hand-write MathML, never use `$` or `$$`, and never leave a formula as bare LaTeX or
  plain ASCII like `c = Q / (m T)`.
- Keep display math in its own block element, and stay inside the supported LaTeX subset:
  `\frac`, `\dfrac`, `\sqrt`, `^`, `_`, `\Delta`, `\times`, `\cdot`, `\pm`, `\rightarrow`,
  `\mathrm{...}`, `\text{...}`, and `\,` for spacing. siunitx (`\SI`), mhchem (`\ce`), and
  `\textdegree` render as literal text, so write units as `\mathrm{J\,kg^{-1}\,K^{-1}}`.
- The guide may use one or more inline `<script data-guide-controls>` blocks for bounded,
  deterministic offline interactions. JavaScript is optional per component, but when used
  it must have a complete static/no-JS fallback and must obey the safe interaction contract.
- Guide scripts must not use fetch, XMLHttpRequest, WebSocket, import, eval, Function,
  innerHTML, insertAdjacentHTML, string-to-code timers, external resources, or untrusted
  HTML injection. Use fixed DOM nodes, textContent, native controls, and local state.
- No remote assets: fonts are embedded, and nothing loads from the network.
- No event-handler attributes such as onclick; bind behavior from the guide script.
- Every diagram `<svg>` needs role="img", with `<title>` as its first child, non-empty
  `<title>` and `<desc>`, and aria-labelledby naming those two ids in that order.
- Use reduced-motion and print fallbacks; controls must be labelled and keyboard accessible.
"""


def build_initial_message(
    source_kind: str, item_count: int, selection: Mapping[str, object]
) -> str:
    source_description = _source_description(source_kind, item_count, selection)
    return (
        f"Source: {source_description}\n\n"
        "Create ONE self-contained HTML study guide from the attached source material. "
        "Use the fixed Diagram-design system and study-guide reference in your system context "
        "to decide the guide's structure, diagrams, typography, visual hierarchy, and bounded "
        "offline interactions. Do not ask for or rely on any additional topic or writing request.\n\n"
        "Include one concise, human-readable <title> in the HTML <head>; that title will be "
        "used as the guide name.\n\n"
        + OUTPUT_POLICY
    )


def build_revision_message(selected_text: str, instruction: str, current_html: str) -> str:
    return (
        "Revise the current study guide using the selected text and the requested change. "
        "Return the full revised HTML document, not a fragment or a diff. Preserve the study-guide "
        "architecture and any valid offline interactions unless the requested change replaces them.\n\n"
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
        "safe-interaction, print, and reduced-motion output policies below.\n\n"
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

INSTRUCTION = r"""Subject: {label}
Attached: {count} page image(s) of the source material, in order.
Page count: {count} pages.

Read the pages, then produce ONE self-contained HTML study artifact for this unit,
following the design system and study-guide architecture in your instructions.

Hard requirements:
- Return the raw HTML document only. No markdown fences, no commentary.
- Build a coherent learning journey: orientation, conceptual spine, numbered sections,
  meaningful visuals, exam/application guidance, retrieval practice, and closing recall.
- Write every equation as LaTeX between `\(` and `\)` (inline) or `\[` and `\]` (display,
  in its own block element). The build step converts them to native MathML, so never
  hand-write MathML, never use `$` or `$$`, and never leave a formula as plain ASCII.
- Read `study-guide.md` before writing and read relevant diagram references before drawing.
- Bounded inline `<script data-guide-controls>` interactions are allowed only when they improve
  understanding. Every interaction needs a complete static/no-JS fallback, local deterministic
  state, accessible controls, and print/reduced-motion behavior.
- Guide scripts must not use fetch, XMLHttpRequest, WebSocket, import, eval, Function,
  innerHTML, insertAdjacentHTML, string-to-code timers, external resources, or untrusted
  HTML injection. Do not use onclick or other executable attributes.
- No remote assets: fonts are embedded, and nothing loads from the network.
- Every diagram `<svg>` needs role="img", with `<title>` as its first child, non-empty
  `<title>` and `<desc>`, and aria-labelledby naming those two ids in order.
"""


def build_unit_message(unit_label: str, page_numbers: Sequence[int]) -> str:
    return INSTRUCTION.format(label=unit_label, count=len(page_numbers))
