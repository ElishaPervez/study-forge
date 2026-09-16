from __future__ import annotations

from collections.abc import Sequence

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
