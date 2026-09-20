from __future__ import annotations

import json
import os
import re
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from html.parser import HTMLParser
from pathlib import Path
from threading import Event

from backend.diagnostics.trace import record, span
from backend.fonts.embed import inject_fonts
from backend.generate.progress import (
    ACTIVITY_CHECKING,
    ACTIVITY_CORRECTING,
    ACTIVITY_REFERENCES,
    ACTIVITY_RETRYING,
    ACTIVITY_WAITING,
    ProgressReporter,
)
from backend.ingest.pdf import InputImage
from backend.jobs.schema import Selection, SourceKind
from backend.llm.client import LLM, LLMError, LLMReply, LLMStopped, ToolCall, image_part
from backend.mathml.render import render_math
from backend.prompt.build import build_initial_message, build_unit_message
from backend.skill.bundle import Bundle
from backend.skill.manifest import get_reference
from backend.verify.self_check import CheckResult, run_self_check

MAX_CALLS = 3
# Reference lookups are not generation attempts. Sharing one budget let the model
# spend every call reading references and never get to write, or repair, an artifact.
MAX_TOOL_TURNS = 6
# A repair can be expressed as exact-text edits instead of a rewritten document.
# Two rounds is enough to recover from an over-broad match; past that, another
# round trip costs more than naming the whole document would have.
MAX_PATCHES = 2
PATCH_TOOL = "apply_edits"
STOPPED_FINDING = "work was stopped before a guide was completed"
RETRY_BACKOFF_BASE_SECONDS = 0.1
RETRY_BACKOFF_MAX_SECONDS = 1.0
# Findings are traced as text, not only as a count. A run that repairs itself
# publishes with an empty finding list, so the rejected artifact's messages are
# the only record of why the repair happened - and were previously thrown away.
FINDING_LOG_LIMIT = 400
FINDING_LOG_MAX = 12
V1_FORBIDDEN_TAGS = {"base", "embed", "object", "iframe"}
ALLOWED_SCRIPT_ATTRIBUTES = {
    "data-guide-controls",
    "data-diagram-controls",
}
FORBIDDEN_SCRIPT_PATTERNS = (
    (r"\bfetch\s*\(", "network fetch calls"),
    (r"\bXMLHttpRequest\b", "XMLHttpRequest"),
    (r"\bWebSocket\b", "WebSocket"),
    (r"\bimport\s*(?:\(|[\\'\"])", "dynamic imports"),
    (r"\beval\s*\(", "eval"),
    (r"\bFunction\s*\(", "Function constructor"),
    (r"\bnew\s+Function\b", "Function constructor"),
    (r"\binnerHTML\b", "innerHTML injection"),
    (r"\binsertAdjacentHTML\b", "insertAdjacentHTML injection"),
    (r"\bdocument\.write\s*\(", "document.write injection"),
    (r"\b(?:setTimeout|setInterval)\s*\(\s*[\\'\"]", "string-to-code timers"),
    (r"\bnavigator\.sendBeacon\b", "beacon network calls"),
    (r"\bdocument\.cookie\b", "cookie access"),
    (r"\b(?:https?:)?//", "remote URL references"),
)
V1_URL_ATTRIBUTES = {
    "src",
    "href",
    "xlink:href",
    "poster",
    "srcset",
    "action",
    "formaction",
}
_CSS_ESCAPE_RE = re.compile(
    r"""\\(?:(?P<hex>[0-9a-fA-F]{1,6})(?:[ \t]|\r\n|[\r\n\f])?|(?P<char>[^\r\n\f]))"""
)
_CSS_IMPORT_RE = re.compile(r"@import\b", re.IGNORECASE)
_CSS_URL_RE = re.compile(
    r"""url\(\s*(?:"(?P<double>[^"]*)"|'(?P<single>[^']*)'|(?P<bare>[^)]*))\s*\)""",
    re.IGNORECASE | re.DOTALL,
)
_EXECUTABLE_URL_PREFIXES = (
    "javascript:",
    "vbscript:",
    "livescript:",
    "mocha:",
    "data:text/html",
    "data:application/xhtml+xml",
    "data:application/javascript",
    "data:text/javascript",
    "data:application/ecmascript",
    "data:text/ecmascript",
)


def _decode_css_escapes(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        hexadecimal = match.group("hex")
        if hexadecimal is None:
            return match.group("char") or ""
        codepoint = int(hexadecimal, 16)
        if codepoint == 0 or codepoint > 0x10FFFF:
            return "\uFFFD"
        return chr(codepoint)

    return _CSS_ESCAPE_RE.sub(replace, value)


def _url_policy_finding(value: str, *, css: bool) -> str | None:
    lowered = value.strip().casefold()
    if lowered.startswith("#"):
        return None
    if lowered.startswith(_EXECUTABLE_URL_PREFIXES):
        return "v1 output policy forbids executable URL schemes"
    if lowered.startswith(("http://", "https://", "//")):
        if css:
            return "v1 output policy forbids remote CSS URLs"
        return "v1 output policy forbids remote assets"
    if lowered.startswith("data:"):
        if css and lowered.startswith("data:image/"):
            return None
        if css and lowered.startswith("data:font/woff2;base64,"):
            return None
        if not css and lowered.startswith("data:image/"):
            return None
        if css:
            return "v1 output policy forbids non-image or embedded-font CSS data URLs"
        return "v1 output policy forbids non-image data URLs on tags"
    return "v1 output policy forbids relative or local asset/style URLs"


def _srcset_urls(value: str) -> list[str]:
    urls: list[str] = []
    index = 0
    length = len(value)
    while index < length:
        while index < length and (value[index].isspace() or value[index] == ","):
            index += 1
        if index >= length:
            break

        start = index
        if value[index : index + 5].casefold() == "data:":
            header_end = value.find(",", index)
            if header_end == -1:
                while index < length and not value[index].isspace():
                    index += 1
            else:
                index = header_end + 1
                while index < length and not value[index].isspace() and value[index] != ",":
                    index += 1
        else:
            while index < length and not value[index].isspace() and value[index] != ",":
                index += 1
        if start < index:
            urls.append(value[start:index])

        while index < length and value[index] != ",":
            index += 1
        if index < length:
            index += 1
    return urls


def _srcset_policy_findings(value: str) -> list[str]:
    findings: list[str] = []
    for candidate in _srcset_urls(value):
        finding = _url_policy_finding(candidate, css=False)
        if finding is not None and finding not in findings:
            findings.append(finding)
    return findings


def _css_policy_findings(css: str) -> list[str]:
    findings: list[str] = []
    decoded_css = _decode_css_escapes(css)
    if _CSS_IMPORT_RE.search(decoded_css):
        findings.append("v1 output policy forbids CSS @import rules")
    for match in _CSS_URL_RE.finditer(decoded_css):
        value = next(
            group for group in match.groups() if group is not None
        )
        finding = _url_policy_finding(value, css=True)
        if finding and finding not in findings:
            findings.append(finding)
    return findings


class UnitStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    VERIFYING = "verifying"
    REPAIRING = "repairing"
    OK = "ok"
    NEEDS_ATTENTION = "needs-attention"
    FAILED = "failed"


@dataclass(frozen=True)
class UnitRequest:
    unit_id: str
    label: str
    page_numbers: list[int]
    pages: list[InputImage]
    source_kind: SourceKind = "pdf"
    selection: Selection | None = None


@dataclass(frozen=True)
class UnitResult:
    status: UnitStatus
    calls: int
    requested_refs: list[str] = field(default_factory=list)
    artifact_path: Path | None = None
    findings: list[str] = field(default_factory=list)


class _V1OutputPolicyParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.findings: list[str] = []
        self._html_seen = False
        self._html_closed = False
        self._inside_html = False
        self._style_depth = 0
        self._css_chunks: list[str] = []
        self.scripts: list[dict[str, object]] = []
        self._current_script: dict[str, object] | None = None

    def _add(self, finding: str) -> None:
        if finding not in self.findings:
            self.findings.append(finding)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        normalized = [(key.casefold(), value or "") for key, value in attrs]
        attributes = dict(normalized)

        if tag == "html":
            if self._html_seen:
                self._add("v1 output policy requires one complete <html> document")
            else:
                self._html_seen = True
                self._inside_html = True
        elif not self._inside_html:
            self._add(f"v1 output policy forbids <{tag}> outside the complete <html> document")

        if tag == "script":
            script = {"attrs": normalized, "body": []}
            self.scripts.append(script)
            self._current_script = script
            attr_names = [key for key, _value in normalized]
            if len(attr_names) != 1 or attr_names[0] not in ALLOWED_SCRIPT_ATTRIBUTES:
                self._add(
                    "v1 output policy allows only <script data-guide-controls> or "
                    "the canonical <script data-diagram-controls>"
                )
        if tag in V1_FORBIDDEN_TAGS:
            self._add(f"v1 output policy forbids <{tag}> tags")

        for key, value in normalized:
            if key.startswith("on"):
                self._add(f"v1 output policy forbids executable attribute {key}")
            if key == "srcdoc":
                self._add("v1 output policy forbids srcdoc attributes")
            if key == "srcset":
                for finding in _srcset_policy_findings(value):
                    self._add(finding)
            elif key in V1_URL_ATTRIBUTES:
                finding = _url_policy_finding(value, css=False)
                if finding:
                    self._add(finding)
            if key == "style":
                self._css_chunks.append(value)

        if tag == "link" and attributes.get("href", "").strip().casefold().startswith(
            ("http://", "https://", "//")
        ):
            self._add("v1 output policy forbids remote stylesheets")
        if tag == "style":
            self._style_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "html":
            if not self._html_seen or self._html_closed:
                self._add("v1 output policy requires one complete <html> document")
            else:
                self._html_closed = True
                self._inside_html = False
        elif not self._inside_html:
            self._add(f"v1 output policy forbids </{tag}> outside the complete <html> document")
        if tag == "script":
            self._current_script = None
        if tag == "style" and self._style_depth:
            self._style_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._current_script is not None:
            body = self._current_script["body"]
            assert isinstance(body, list)
            body.append(data)
        if self._style_depth:
            self._css_chunks.append(data)
        if data.strip() and not self._inside_html:
            self._add("v1 output policy forbids text outside the complete <html> document")


def _v1_output_policy_findings(html: str) -> list[str]:
    parser = _V1OutputPolicyParser()
    parser.feed(html)
    parser.close()
    if not parser._html_seen:
        parser._add("v1 output policy requires one complete <html> document")
    elif not parser._html_closed:
        parser._add("v1 output policy requires a closing </html> tag")
    for finding in _css_policy_findings("".join(parser._css_chunks)):
        parser._add(finding)
    for script in parser.scripts:
        body = "".join(str(part) for part in script["body"])
        for pattern, label in FORBIDDEN_SCRIPT_PATTERNS:
            if re.search(pattern, body, re.IGNORECASE):
                parser._add(f"v1 output policy forbids {label} in inline guide scripts")
    return parser.findings


def _tools(bundle: Bundle, *, allow_edits: bool = False) -> list[dict]:
    names = sorted(bundle.references)
    listing = "; ".join(f"{name} ({bundle.references[name].title})" for name in names)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "list_references",
                "description": "List the available design-system reference files.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_reference",
                "description": f"Read one reference. Valid names: {listing}",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": names},
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
        },
    ]
    if allow_edits:
        # Offered only once a document exists to patch, so a first draft cannot
        # be "edited" into being. An edit carries a few dozen tokens where a
        # rewritten document carries tens of thousands.
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": PATCH_TOOL,
                    "description": (
                        "Repair the current document in place with exact-text replacements. "
                        "Each find must appear in the document exactly once. Prefer this "
                        "over writing the document out again."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "edits": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "find": {
                                            "type": "string",
                                            "description": "exact text from the document",
                                        },
                                        "replace": {
                                            "type": "string",
                                            "description": "text to put in its place",
                                        },
                                    },
                                    "required": ["find", "replace"],
                                    "additionalProperties": False,
                                },
                            }
                        },
                        "required": ["edits"],
                        "additionalProperties": False,
                    },
                },
            }
        )
    return tools


def _malformed_arguments(arguments: object) -> str | None:
    if not isinstance(arguments, dict):
        return "malformed tool arguments"
    return None


def _apply_edits(document: str, arguments: object) -> tuple[str | None, str]:
    """Replace exact text in the document. Every edit must match or nothing is applied.

    Matching is deliberately strict. A patch that quietly matched the wrong span, or
    two spans, would corrupt a document that already passed every other gate, so an
    ambiguous find is refused and the model is told which edit failed and why.
    """
    if not isinstance(arguments, dict):
        return None, "malformed tool arguments: apply_edits needs an edits list"
    edits = arguments.get("edits")
    if not isinstance(edits, list) or not edits:
        return None, "malformed tool arguments: apply_edits needs a non-empty edits list"
    patched = document
    for position, edit in enumerate(edits, start=1):
        if not isinstance(edit, dict):
            return None, f"edit {position} is not an object with find and replace"
        find = edit.get("find")
        replace = edit.get("replace")
        if not isinstance(find, str) or not find:
            return None, f"edit {position} needs a non-empty find string"
        if not isinstance(replace, str):
            return None, f"edit {position} needs a replace string"
        occurrences = patched.count(find)
        if occurrences == 0:
            return None, f"edit {position} matched no text; copy the document exactly"
        if occurrences > 1:
            return None, f"edit {position} matched {occurrences} times; add more context"
        patched = patched.replace(find, replace, 1)
    return patched, f"applied {len(edits)} edit(s)"


def _patched_document(
    calls: Sequence[ToolCall], document: str | None, patches_used: int
) -> tuple[str | None, str]:
    """Apply this turn's patch request to the current document, if it made one."""
    patch = next((call for call in calls if call.name == PATCH_TOOL), None)
    if patch is None:
        return None, ""
    if document is None:
        return None, "there is no document to patch yet; return the complete HTML document"
    if patches_used >= MAX_PATCHES:
        return None, (
            f"the patch limit ({MAX_PATCHES}) is used up; "
            "return the complete corrected HTML document instead"
        )
    try:
        arguments = json.loads(patch.arguments or "{}")
    except (json.JSONDecodeError, TypeError):
        return None, "malformed tool arguments: apply_edits needs an edits list"
    return _apply_edits(document, arguments)


def _resolve_tool_calls(
    calls: Sequence[ToolCall],
    bundle: Bundle,
    requested: list[str],
    *,
    patch_note: str | None = None,
) -> list[dict]:
    results: list[dict] = []
    for call in calls:
        if call.name == PATCH_TOOL:
            results.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": patch_note or "edits were not applied",
                }
            )
            continue
        if call.name not in {"list_references", "read_reference"}:
            content = f"unknown tool: {call.name!r}"
            results.append({"role": "tool", "tool_call_id": call.id, "content": content})
            continue

        try:
            arguments = json.loads(call.arguments or "{}")
        except (json.JSONDecodeError, TypeError):
            content = "malformed tool arguments"
        else:
            content = _malformed_arguments(arguments)
            if content is None and call.name == "list_references":
                content = "\n".join(
                    f"{key}: {reference.title} — {reference.summary}"
                    for key, reference in sorted(bundle.references.items())
                )
            elif content is None:
                name = arguments.get("name")
                if not isinstance(name, str) or not name:
                    content = "malformed tool arguments: read_reference requires a name"
                else:
                    try:
                        reference = get_reference(bundle.references, name)
                    except KeyError:
                        content = f"unknown reference: {name!r}. Use list_references."
                    else:
                        requested.append(name)
                        try:
                            with span("tool.read_reference", name=name) as read:
                                content = reference.path.read_text(encoding="utf-8")
                                read["chars"] = len(content)
                        except OSError as error:
                            content = f"reference unavailable: {error}"
        results.append({"role": "tool", "tool_call_id": call.id, "content": content})
    return results


_DOCTYPE_RE = re.compile(r"<!doctype[^>]*>", re.IGNORECASE)
_HTML_OPEN_RE = re.compile(r"<html[\s>]", re.IGNORECASE)
_HTML_CLOSE_RE = re.compile(r"</html\s*>", re.IGNORECASE)


def _document_span(text: str) -> str | None:
    """Slice out the document itself, ignoring anything around it."""
    opening = _HTML_OPEN_RE.search(text)
    if opening is None:
        return None
    closing = None
    for match in _HTML_CLOSE_RE.finditer(text):
        closing = match
    if closing is None or closing.end() <= opening.start():
        return None

    start = opening.start()
    doctype = None
    for match in _DOCTYPE_RE.finditer(text, 0, opening.start()):
        doctype = match
    if doctype is not None and not text[doctype.end() : opening.start()].strip():
        start = doctype.start()
    return text[start : closing.end()]


def _strip_fences(text: str) -> str:
    """Return the document itself from a reply that may add commentary or a fence.

    The model likes to introduce its answer ("Here is the guide:") and wrap the
    document in a markdown fence. The output policy rejects text outside the
    document, so leaving that in spends a whole repair call deleting it.
    """
    stripped = text.strip()
    document = _document_span(stripped)
    if document is not None:
        return document.strip() + "\n"
    if stripped.startswith("```"):
        lines = stripped.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped + "\n"


def _write_atomic(path: Path, text: str) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(text)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _build_artifact(
    document_text: str, *, artifact_path: Path, fonts_css: str
) -> tuple[str, str]:
    """Strip, render math, embed fonts and write. Returns the HTML and its source.

    A patched document takes this same path as a freshly written one, so an edited
    artifact is built and measured exactly like a rewritten one.
    """
    with span("artifact.build", reply_chars=len(document_text)) as build:
        with span("artifact.strip_fences"):
            document = _strip_fences(document_text)
        with span("artifact.mathml", chars=len(document)) as mathml:
            rendered = render_math(document)
            mathml["html_chars"] = len(rendered)
            mathml["math_elements"] = rendered.count("<math")
        with span("artifact.fonts", chars=len(rendered)) as fonts:
            html = inject_fonts(rendered, fonts_css)
            fonts["html_chars"] = len(html)
        build["html_chars"] = len(html)
        with span("artifact.write", chars=len(html), bytes=len(html.encode("utf-8"))):
            _write_atomic(artifact_path, html)
    return html, document


def _verify_artifact(
    html: str,
    artifact_path: Path,
    bundle: Bundle,
    checker: Callable[[Path, Path], CheckResult],
) -> list[str]:
    """Policy plus the skill's own checker. An empty list means it is publishable."""
    with span("artifact.policy", chars=len(html)) as policy:
        policy_findings = _v1_output_policy_findings(html)
        policy["findings"] = len(policy_findings) or None
        policy["messages"] = logged_findings(policy_findings) or None
    with span("artifact.checker") as checking:
        result = checker(artifact_path, bundle.skill_dir)
        checking["ok"] = result.ok
        checking["findings"] = len(result.findings) or None
        checking["messages"] = logged_findings(result.findings) or None
    return [*policy_findings, *result.findings]


def _empty_reply_finding(reply: LLMReply) -> str:
    """Explain a completion that carried no text, instead of blaming an artifact."""
    if reply.finish_reason == "length":
        return (
            "model exhausted its output budget before writing any HTML "
            f"(finish_reason=length, reasoning_tokens={reply.reasoning_tokens} of "
            f"{reply.completion_tokens} completion tokens)"
        )
    return f"model returned no content (finish_reason={reply.finish_reason or 'unknown'})"


def _truncated_reply_finding(reply: LLMReply) -> str:
    """Say a document was cut short, so an incomplete artifact is not mistaken for a bad one."""
    return (
        "model stopped at the output budget mid-document, so the artifact is incomplete "
        f"(finish_reason=length, {reply.completion_tokens} completion tokens, "
        f"{reply.reasoning_tokens} of them reasoning)"
    )


def logged_findings(findings: Sequence[str]) -> list[str]:
    """Findings for the trace: enough text to diagnose, bounded in the JSONL."""
    kept = [
        finding if len(finding) <= FINDING_LOG_LIMIT else finding[: FINDING_LOG_LIMIT - 3] + "..."
        for finding in findings[:FINDING_LOG_MAX]
    ]
    if len(findings) > FINDING_LOG_MAX:
        kept.append(f"... and {len(findings) - FINDING_LOG_MAX} more")
    return kept


def _empty_reply_prompt(finding: str) -> str:
    return (
        "Your previous reply contained no HTML at all, so there was nothing to verify:\n"
        f"- {finding}\n\n"
        "Answer with the complete HTML document itself. Preserve the offline and "
        "accessible SVG requirements from the original request."
    )


def _repair_prompt(findings: Sequence[str]) -> str:
    details = "\n".join(f"- {finding}" for finding in findings)
    return (
        "The artifact failed verification:\n"
        f"{details}\n\n"
        f"Fix only what failed, by calling {PATCH_TOOL} with exact-text replacements "
        "against the document as you wrote it, and leave everything else untouched. "
        "Return the corrected complete HTML document only if the repair cannot be "
        "expressed as edits. Preserve the offline and accessible SVG requirements "
        "from the original request."
    )


def stopped_work(stop: Event | None) -> bool:
    return stop is not None and stop.is_set()


def _backoff_seconds(call_number: int) -> float:
    return min(
        RETRY_BACKOFF_BASE_SECONDS * (2 ** max(call_number - 1, 0)),
        RETRY_BACKOFF_MAX_SECONDS,
    )


def _failed_result(
    status: UnitStatus,
    calls: int,
    requested: list[str],
    artifact_path: Path,
    published: bool,
    findings: Sequence[str],
) -> UnitResult:
    return UnitResult(
        status,
        calls,
        list(requested),
        artifact_path if published else None,
        list(findings),
    )


def generate_unit(
    request: UnitRequest,
    *,
    llm: LLM,
    bundle: Bundle,
    fonts_css: str,
    out_dir: Path,
    max_calls: int = MAX_CALLS,
    checker: Callable[[Path, Path], CheckResult] = run_self_check,
    status_callback: Callable[[UnitStatus], None] | None = None,
    progress: ProgressReporter | None = None,
    stop: Event | None = None,
) -> UnitResult:
    budget = max(0, min(MAX_CALLS, max_calls))
    calls = 0
    requested: list[str] = []
    last_findings: list[str] = []
    artifacts = out_dir / "units" / request.unit_id
    artifacts.mkdir(parents=True, exist_ok=True)
    artifact_path = artifacts / "artifact.html"
    published = False

    content: list[dict] = []
    with span("payload.images", count=len(request.pages)) as encoding:
        for image in request.pages:
            content.append(image_part(image))
        encoding["payload_chars"] = sum(
            len(part["image_url"]["url"]) for part in content
        )
    initial_message = (
        build_initial_message(request.source_kind, len(request.pages), request.selection)
        if request.selection is not None
        else build_unit_message(request.label, request.page_numbers)
    )
    content.append(
        {"type": "text", "text": initial_message}
    )
    messages: list[dict] = [
        {"role": "system", "content": bundle.system_prompt},
        {"role": "user", "content": content},
    ]
    repair_used = False
    attempts = 0
    tool_turns = 0
    patches_used = 0
    # The document a patch would edit. Set as soon as something has been built, so
    # the repair turn can offer edits instead of a full rewrite.
    candidate: str | None = None

    while attempts < budget:
        if stopped_work(stop):
            return _failed_result(
                UnitStatus.FAILED, calls, requested, artifact_path, published, [STOPPED_FINDING]
            )
        if tool_turns >= MAX_TOOL_TURNS:
            last_findings = [
                "tool turns exhausted before the artifact passed verification"
                if candidate is not None
                else "reference lookups exhausted their allowance before any HTML was written"
            ]
            break
        calls += 1
        if progress is not None:
            progress.begin_attempt()
            progress.set_activity(ACTIVITY_WAITING)
        attempt = attempts + 1
        record(
            "llm.call.started",
            call=calls,
            attempt=attempt,
            tool_turn=tool_turns,
            messages=len(messages),
            repair=repair_used,
        )
        try:
            with span(
                "llm.attempt",
                call=calls,
                attempt=attempt,
                messages=len(messages),
                repair=repair_used,
            ) as attempt_span:
                reply: LLMReply = llm.complete(
                    messages,
                    tools=_tools(bundle, allow_edits=candidate is not None),
                    on_text=progress.observe if progress is not None else None,
                    stop=stop,
                )
                attempt_span["text_chars"] = len(reply.text or "")
                attempt_span["tool_calls"] = len(reply.tool_calls) or None
                attempt_span["finish_reason"] = reply.finish_reason or None
        except LLMStopped:
            return _failed_result(
                UnitStatus.FAILED, calls, requested, artifact_path, published, [STOPPED_FINDING]
            )
        except LLMError as error:
            attempts += 1
            error_finding = str(error)
            if not error.retryable or attempts >= budget:
                findings = [*last_findings, error_finding]
                return _failed_result(
                    UnitStatus.FAILED,
                    calls,
                    requested,
                    artifact_path,
                    published,
                    findings,
                )
            if progress is not None:
                progress.set_activity(ACTIVITY_RETRYING)
            backoff = _backoff_seconds(calls)
            record("llm.retry_backoff", seconds=backoff, error=error_finding)
            time.sleep(backoff)
            continue
        record(
            "llm.call.finished",
            call=calls,
            text_chars=len(reply.text or "") or None,
            tool_calls=len(reply.tool_calls) or None,
            finish_reason=reply.finish_reason or None,
        )

        if reply.tool_calls:
            tool_turns += 1
            patch_note: str | None = None
            patched, note = _patched_document(reply.tool_calls, candidate, patches_used)
            if patched is not None:
                patches_used += 1
                record("repair.patched", call=calls, edits=patches_used)
                html, candidate = _build_artifact(
                    patched, artifact_path=artifact_path, fonts_css=fonts_css
                )
                published = True
                if status_callback is not None:
                    status_callback(UnitStatus.VERIFYING)
                findings = _verify_artifact(html, artifact_path, bundle, checker)
                if not findings:
                    return UnitResult(UnitStatus.OK, calls, list(requested), artifact_path, [])
                # The patch is kept on disk and reported like any rejected build: the
                # model gets the new findings and may edit again or rewrite outright.
                last_findings = findings
                record(
                    "artifact.rejected",
                    call=calls,
                    findings=len(findings),
                    messages=logged_findings(findings),
                )
                note = (
                    "the edits were applied, but the artifact still fails verification:\n"
                    + "\n".join(f"- {finding}" for finding in findings)
                )
            patch_note = note or None
            if progress is not None:
                # A tool turn is not a new draft: its provisional count is dropped.
                progress.discard_attempt_progress()
                progress.set_activity(
                    ACTIVITY_CORRECTING if patched is not None else ACTIVITY_REFERENCES
                )
            messages.append(
                {
                    "role": "assistant",
                    "content": reply.text or "",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": call.arguments},
                        }
                        for call in reply.tool_calls
                    ],
                }
            )
            messages.extend(
                _resolve_tool_calls(reply.tool_calls, bundle, requested, patch_note=patch_note)
            )
            continue

        attempts += 1
        if not (reply.text or "").strip():
            # No text is not an artifact: publishing "\n" here produced a one-byte
            # file whose empty contents were then reported as policy violations.
            last_findings = [_empty_reply_finding(reply)]
            if repair_used or attempts >= budget:
                return _failed_result(
                    UnitStatus.NEEDS_ATTENTION,
                    calls,
                    requested,
                    artifact_path,
                    published,
                    last_findings,
                )
            repair_used = True
            messages.append({"role": "user", "content": _empty_reply_prompt(last_findings[0])})
            if status_callback is not None:
                status_callback(UnitStatus.REPAIRING)
            if progress is not None:
                progress.set_activity(ACTIVITY_CORRECTING)
            record("repair.empty_reply", call=calls, finding=last_findings[0])
            continue

        if stopped_work(stop):
            return _failed_result(
                UnitStatus.FAILED, calls, requested, artifact_path, published, [STOPPED_FINDING]
            )
        html, candidate = _build_artifact(
            reply.text or "", artifact_path=artifact_path, fonts_css=fonts_css
        )
        published = True
        if status_callback is not None:
            status_callback(UnitStatus.VERIFYING)
        if progress is not None:
            progress.set_activity(ACTIVITY_CHECKING)
        findings = _verify_artifact(html, artifact_path, bundle, checker)
        if not findings:
            return UnitResult(UnitStatus.OK, calls, list(requested), artifact_path, [])

        if reply.finish_reason == "length":
            findings.append(_truncated_reply_finding(reply))
        last_findings = findings or ["v1 output policy or checker rejected artifact"]
        record(
            "artifact.rejected",
            call=calls,
            findings=len(last_findings),
            messages=logged_findings(last_findings),
        )
        if repair_used or attempts >= budget:
            return _failed_result(
                UnitStatus.NEEDS_ATTENTION,
                calls,
                requested,
                artifact_path,
                published,
                last_findings,
            )

        repair_used = True
        messages.append({"role": "assistant", "content": reply.text or ""})
        messages.append({"role": "user", "content": _repair_prompt(last_findings)})
        if status_callback is not None:
            status_callback(UnitStatus.REPAIRING)
        if progress is not None:
            progress.set_activity(ACTIVITY_CORRECTING)
        record(
            "repair.requested",
            call=calls,
            findings=len(last_findings),
            messages=logged_findings(last_findings),
        )

    return _failed_result(
        UnitStatus.NEEDS_ATTENTION,
        calls,
        requested,
        artifact_path,
        published,
        last_findings or ["call budget exhausted before an artifact was returned"],
    )
