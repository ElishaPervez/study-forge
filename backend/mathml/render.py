"""Turn the LaTeX a guide author writes into native MathML.

The artifact stays single-file and offline, so math is rendered here, at generation
time, rather than by a runtime library: the output is plain MathML that the browser
already knows how to typeset, with no script, no math web font, and nothing for the
output policy to reject.

Only text is touched. Markup, attributes, and the contents of verbatim elements
(``script``, ``style``, ``svg``, ``math``, ``code``, ``pre``, ``textarea``, ``title``)
are copied through byte for byte.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree
from html.parser import HTMLParser

import latex2mathml.converter

# Text inside these elements is verbatim: a delimiter in a script, a stylesheet, an
# SVG label, or MathML that is already rendered must be left exactly as written.
VERBATIM_ELEMENTS = frozenset(
    {"script", "style", "svg", "math", "code", "pre", "textarea", "title"}
)

# latex2mathml copies a literal "<" or "&" out of \text{...}, which would corrupt the
# document. Only these element names may keep a raw "<"; anything else is escaped.
MATHML_ELEMENTS = (
    "math",
    "menclose",
    "mfrac",
    "mi",
    "mn",
    "mo",
    "mover",
    "mpadded",
    "mphantom",
    "mroot",
    "mrow",
    "mspace",
    "msqrt",
    "mstyle",
    "msub",
    "msubsup",
    "msup",
    "mtable",
    "mtd",
    "mtext",
    "mtr",
    "munder",
    "munderover",
)
_TAG_RE = re.compile(r"</?(?:" + "|".join(MATHML_ELEMENTS) + r")(?=[\s/>])", re.IGNORECASE)
_ENTITY_RE = re.compile(r"&#[0-9]+;|&#[xX][0-9A-Fa-f]+;")

# A single "$" is ambiguous ("$5 and $10"), so it only opens math when its body carries
# an unmistakable LaTeX signal. "\(", "\[", and "$$" are never ambiguous.
_DOLLAR_SIGNAL_RE = re.compile(r"[\\^_={}]")

# latex2mathml 3.81.1 maps \circ to U+2218 RING OPERATOR, which the MathML operator
# dictionary classifies as infix, so a browser pads it and "25^\circ\text{C}" typeset as
# "25 ° C". A superscript \circ always means degrees here, so it becomes the tight, upright
# U+00B0 instead; a bare \circ (function composition) keeps its ring operator.
_DEGREE_RE = re.compile(r"\^\s*\{?\s*\\circ(?![A-Za-z])\s*\}?")
_DEGREE_LATEX = "^\\text{\u00b0}"

# latex2mathml also drops mathvariant="normal" when \mathrm{} wraps a single character, so
# \mathrm{J} came out italic while \mathrm{kg} stayed upright and one sentence mixed both.
# The empty group forces the multi-character path, and the empty <mrow/> it leaves behind
# is stripped after conversion.
_SINGLE_CHAR_MATHRM_RE = re.compile(r"\\mathrm\s*\{\s*([^{}\s\\])\s*\}")
_EMPTY_MROW_RE = re.compile(r"<mrow\s*/>")


def _escape_raw_markup(mathml: str) -> str:
    """Escape the "<" and "&" that the converter left as text instead of markup."""
    pieces: list[str] = []
    index = 0
    length = len(mathml)
    while index < length:
        character = mathml[index]
        if character == "<":
            tag = _TAG_RE.match(mathml, index)
            end = mathml.find(">", index) if tag is not None else -1
            if end != -1:
                pieces.append(mathml[index : end + 1])
                index = end + 1
                continue
            pieces.append("&lt;")
        elif character == "&":
            entity = _ENTITY_RE.match(mathml, index)
            if entity is not None:
                pieces.append(entity.group(0))
                index = entity.end()
                continue
            pieces.append("&amp;")
        else:
            pieces.append(character)
        index += 1
    return "".join(pieces)


def _work_around_converter_quirks(latex: str) -> str:
    r"""Repair the latex2mathml defects that mis-render guide math.

    Both fixes are applied to the LaTeX rather than to the MathML, because that is the only
    point where the intent behind a glyph is still known: a superscript \circ is a degree,
    while a bare \circ is an operator. Replacement functions are used because re.sub treats
    backslash escapes in a template as escapes, which would eat a \text command's prefix.
    """
    degrees = _DEGREE_RE.sub(lambda _match: _DEGREE_LATEX, latex)
    return _SINGLE_CHAR_MATHRM_RE.sub(
        lambda match: "\\mathrm{" + match.group(1) + "{}}", degrees
    )


def to_mathml(latex: str, *, display: bool = False) -> str | None:
    """Convert one LaTeX expression, or None when it cannot be rendered safely.

    None means "leave the author's source alone": an expression we cannot render is
    still readable, while malformed markup would break the whole document.
    """
    expression = latex.strip()
    if not expression:
        return None
    try:
        converted = latex2mathml.converter.convert(
            _work_around_converter_quirks(expression),
            display="block" if display else "inline",
        )
    except Exception:  # noqa: BLE001 - a conversion failure must never break a guide
        return None
    repaired = _EMPTY_MROW_RE.sub("", _escape_raw_markup(converted))
    try:
        xml.etree.ElementTree.fromstring(repaired)
    except xml.etree.ElementTree.ParseError:
        return None
    return repaired


def _is_dollar_math(body: str) -> bool:
    if not body or body[0].isspace() or body[-1].isspace() or "\n" in body:
        return False
    return _DOLLAR_SIGNAL_RE.search(body) is not None


def _delimiter_at(text: str, index: int) -> tuple[str, str, bool] | None:
    """Return (opening, closing, display) for a delimiter starting at index."""
    if text.startswith("\\(", index):
        return "\\(", "\\)", False
    if text.startswith("\\[", index):
        return "\\[", "\\]", True
    if text.startswith("$$", index):
        return "$$", "$$", True
    if text[index] == "$":
        closing = text.find("$", index + 1)
        if closing != -1 and _is_dollar_math(text[index + 1 : closing]):
            return "$", "$", False
    return None


def render_text(text: str) -> str:
    """Replace every LaTeX delimiter pair in one run of HTML text with MathML."""
    pieces: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        delimiter = _delimiter_at(text, index)
        if delimiter is not None:
            opening, closing, display = delimiter
            closing_index = text.find(closing, index + len(opening))
            if closing_index != -1:
                mathml = to_mathml(text[index + len(opening) : closing_index], display=display)
                if mathml is not None:
                    pieces.append(mathml)
                    index = closing_index + len(closing)
                    continue
        character = text[index]
        pieces.append(character)
        # A backslash that did not open a delimiter ("\$", "\\") owns the next
        # character, so it can never be mistaken for one on the following step.
        if character == "\\" and index + 1 < length:
            pieces.append(text[index + 1])
            index += 2
            continue
        index += 1
    return "".join(pieces)


class _TextRegionCollector(HTMLParser):
    """Records the exact offsets of every text run, so only text is ever rewritten."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self._source = source
        self._line_starts = [0]
        for index, character in enumerate(source):
            if character == "\n":
                self._line_starts.append(index + 1)
        self._stack: list[str] = []
        self._regions: list[tuple[int, int]] = []
        self._region_start: int | None = None

    def _offset(self) -> int:
        line, column = self.getpos()
        line_index = min(max(line - 1, 0), len(self._line_starts) - 1)
        return min(self._line_starts[line_index] + column, len(self._source))

    def _open_region(self) -> None:
        if self._region_start is None and not any(
            name in VERBATIM_ELEMENTS for name in self._stack
        ):
            self._region_start = self._offset()

    def _close_region(self) -> None:
        if self._region_start is not None:
            self._regions.append((self._region_start, self._offset()))
            self._region_start = None

    def text_regions(self) -> list[tuple[int, int]]:
        self._close_region()
        return self._regions

    def handle_data(self, data: str) -> None:
        self._open_region()

    def handle_entityref(self, name: str) -> None:
        self._open_region()

    def handle_charref(self, name: str) -> None:
        self._open_region()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._close_region()
        self._stack.append(tag.casefold())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._close_region()

    def handle_endtag(self, tag: str) -> None:
        self._close_region()
        name = tag.casefold()
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index] == name:
                del self._stack[index:]
                break

    def handle_comment(self, data: str) -> None:
        self._close_region()

    def handle_decl(self, decl: str) -> None:
        self._close_region()

    def handle_pi(self, data: str) -> None:
        self._close_region()

    def unknown_decl(self, data: str) -> None:
        self._close_region()


def render_math(html: str) -> str:
    """Render the LaTeX delimiters in an HTML document's text into native MathML."""
    if "\\(" not in html and "\\[" not in html and "$" not in html:
        return html
    collector = _TextRegionCollector(html)
    collector.feed(html)
    collector.close()
    rendered = html
    for start, end in reversed(collector.text_regions()):
        replacement = render_text(html[start:end])
        if replacement != html[start:end]:
            rendered = rendered[:start] + replacement + rendered[end:]
    return rendered
