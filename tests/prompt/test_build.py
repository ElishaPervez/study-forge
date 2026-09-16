from backend.prompt.build import build_unit_message


def test_unit_message_names_the_pages_and_nothing_volatile() -> None:
    message = build_unit_message("Unit 2 — Cell Division", [7, 8, 9])

    assert "Cell Division" in message
    assert "3 pages" in message
    assert "self-contained" in message
    assert "script" in message.lower()
    # No timestamps, no ids: the message must be stable for prompt caching.
    assert build_unit_message("Unit 2 — Cell Division", [7, 8, 9]) == message
