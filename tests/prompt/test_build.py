from backend.prompt.build import (
    build_initial_message,
    build_revision_message,
    build_unit_message,
    extract_guide_title,
)


def test_unit_message_names_the_pages_and_nothing_volatile() -> None:
    message = build_unit_message("Unit 2 — Cell Division", [7, 8, 9])

    assert "Cell Division" in message
    assert "3 pages" in message
    assert "self-contained" in message
    assert "script" in message.lower()
    # No timestamps, no ids: the message must be stable for prompt caching.
    assert build_unit_message("Unit 2 — Cell Division", [7, 8, 9]) == message


def test_math_instructions_reach_the_model_verbatim() -> None:
    message = build_unit_message("Unit 2 — Cell Division", [7, 8, 9])

    assert "\\(" in message and "\\)" in message
    assert "\\[" in message and "\\]" in message
    assert "MathML" in message
    # A non-raw string would swallow \f as a form feed and \t as a tab.
    assert "\f" not in message
    assert "\t" not in message


def test_initial_message_has_fixed_source_instructions_and_no_custom_prompt() -> None:
    message = build_initial_message("pdf", 3, {"mode": "custom", "start": 7, "end": 9})

    assert "PDF" in message
    assert "7–9" in message
    assert "Diagram-design" in message
    assert "<title>" in message
    assert "user-entered" not in message.lower()
    assert "custom instruction" not in message.lower()


def test_initial_image_message_describes_the_ordered_image_group() -> None:
    message = build_initial_message("images", 2, {"mode": "images"})

    assert "2 original image" in message
    assert "ordered" in message.lower()
    assert "PDF" not in message


def test_extract_guide_title_normalizes_whitespace_and_has_visible_fallback() -> None:
    assert extract_guide_title("<html><head><title>  Cell\n  Division </title></head></html>") == (
        "Cell Division"
    )
    assert extract_guide_title("<html><head></head><body>Guide</body></html>") == "Untitled guide"


def test_extract_guide_title_ignores_svg_accessibility_titles_outside_head() -> None:
    html = """
    <html><head></head><body>
      <svg role="img"><title>Accessible diagram label</title></svg>
    </body></html>
    """

    assert extract_guide_title(html) == "Untitled guide"


def test_revision_message_carries_selection_instruction_and_current_html() -> None:
    message = build_revision_message(
        "mitosis is one kind of cell division",
        "Clarify the difference from meiosis.",
        "<html><head><title>Cells</title></head><body>...</body></html>",
    )

    assert "mitosis is one kind of cell division" in message
    assert "Clarify the difference from meiosis." in message
    assert "<title>Cells</title>" in message
    assert "full revised HTML" in message
    assert "no remote assets" in message.lower()
