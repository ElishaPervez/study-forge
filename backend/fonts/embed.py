from __future__ import annotations

import base64
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

STYLE_OPEN = "<style>"
STYLE_CLOSE = "</style>"


@dataclass(frozen=True)
class FontSpec:
    family: str
    weight: int
    file: str


def _face(fonts_dir: Path, spec: FontSpec) -> str:
    payload = base64.b64encode((fonts_dir / spec.file).read_bytes()).decode("ascii")
    return (
        "@font-face{"
        f"font-family:'{spec.family}';"
        f"font-style:normal;font-weight:{spec.weight};font-display:swap;"
        f"src:url(data:font/woff2;base64,{payload}) format('woff2');"
        "}"
    )


def font_face_css(fonts_dir: Path, specs: Sequence[FontSpec]) -> str:
    return "".join(_face(fonts_dir, spec) for spec in specs)


def inject_fonts(html: str, css: str) -> str:
    """Put the faces in the first <style> block, or add one to <head>.

    It must be CSS, never a tag attribute: self_check.py rejects any
    non-`data:image/` data URL on a tag and never inspects CSS.
    """
    if "@font-face" in html:
        return html
    if STYLE_OPEN in html and STYLE_CLOSE in html:
        return html.replace(STYLE_OPEN, STYLE_OPEN + css, 1)
    head = re.search(r"<head[^>]*>", html, re.IGNORECASE)
    if head is None:
        return html
    return html[: head.end()] + f"<style>{css}</style>" + html[head.end():]
