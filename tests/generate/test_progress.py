from backend.generate.progress import (
    ACTIVITY_CHECKING,
    ACTIVITY_PREPARING,
    ACTIVITY_WRITING,
    ProgressReporter,
    count_document_lines,
)


class FakeClock:
    def __init__(self) -> None:
        self.ticks = 0

    def __call__(self) -> str:
        self.ticks += 1
        return f"t{self.ticks}"


def snapshot(reporter: ProgressReporter):
    return reporter.snapshot()


def test_no_output_reports_zeroes_and_no_output_time() -> None:
    reporter = ProgressReporter(now=FakeClock())

    state = snapshot(reporter)

    assert state.activity == ACTIVITY_PREPARING
    assert state.attempt == 0
    assert state.lines == 0
    assert state.characters == 0
    assert state.last_output_at is None


def test_counts_follow_every_received_piece() -> None:
    reporter = ProgressReporter(now=FakeClock())

    reporter.observe("<ht")
    assert snapshot(reporter).lines == 0
    assert snapshot(reporter).characters == 0
    assert snapshot(reporter).last_output_at is None

    reporter.observe("ml>\n")
    after_open = snapshot(reporter)
    assert after_open.lines == 1
    assert after_open.characters == len("<html>\n")
    assert after_open.activity == ACTIVITY_WRITING
    assert after_open.last_output_at is not None

    reporter.observe("<body>\n")
    assert snapshot(reporter).lines == 2
    assert snapshot(reporter).characters == len("<html>\n<body>\n")

    reporter.observe("<p>a partly written line")
    partly = snapshot(reporter)
    assert partly.lines == 3
    assert partly.characters == len("<html>\n<body>\n<p>a partly written line")

    reporter.observe("</p>\n")
    assert snapshot(reporter).lines == 3
    assert snapshot(reporter).characters == len("<html>\n<body>\n<p>a partly written line</p>\n")
    assert snapshot(reporter).lines == count_document_lines(
        "<html>\n<body>\n<p>a partly written line</p>\n"
    )

    reporter.observe("</html>\n")
    ended = snapshot(reporter)
    assert ended.lines == 4
    assert ended.characters == len("<html>\n<body>\n<p>a partly written line</p>\n</html>")


def test_pieces_are_never_counted_twice() -> None:
    reporter = ProgressReporter(now=FakeClock())
    document = "<html>\n<body>\n<p>text</p>\n</body>\n</html>"

    for index in range(0, len(document), 3):
        reporter.observe(document[index : index + 3])

    state = snapshot(reporter)
    assert state.characters == len(document)
    assert state.lines == len(document.splitlines())


def test_a_line_ending_split_between_pieces_counts_once() -> None:
    reporter = ProgressReporter(now=FakeClock())

    reporter.observe("<html>\r")
    reporter.observe("\n<body>")

    state = snapshot(reporter)
    assert state.characters == len("<html>\r\n<body>")
    assert state.lines == 2


def test_a_very_long_line_keeps_counting_characters() -> None:
    reporter = ProgressReporter(now=FakeClock())
    reporter.observe("<html>\n")

    for _ in range(5):
        reporter.observe("x" * 1000)

    state = snapshot(reporter)
    assert state.lines == 2
    assert state.characters == len("<html>\n") + 5000


def test_counting_starts_at_the_document_opening_not_the_prose() -> None:
    reporter = ProgressReporter(now=FakeClock())

    reporter.observe("Here is the guide you asked for:\n")
    reporter.observe("```html\n")
    assert snapshot(reporter).lines == 0
    assert snapshot(reporter).last_output_at is None

    reporter.observe("<ht")
    reporter.observe("ml>\n<body>\n")
    reporter.observe("</body>\n</html>\n")
    reporter.observe("```\nHope this helps!\n")

    state = snapshot(reporter)
    assert state.characters == len("<html>\n<body>\n</body>\n</html>")
    assert state.lines == 4


def test_a_doctype_immediately_before_the_opening_is_counted() -> None:
    reporter = ProgressReporter(now=FakeClock())

    reporter.observe("<!DOCTYPE html>\n<html>\n<body></body>\n</html>")

    state = snapshot(reporter)
    assert state.characters == len("<!DOCTYPE html>\n<html>\n<body></body>\n</html>")
    assert state.lines == 4


def test_output_after_the_closing_tag_is_ignored() -> None:
    reporter = ProgressReporter(now=FakeClock())
    reporter.observe("<html><body>done</body></html>")

    reporter.observe("\n\n```\nTrailing commentary that is not the document.")
    reporter.observe("<html><body>a second document</body></html>")

    state = snapshot(reporter)
    assert state.characters == len("<html><body>done</body></html>")
    assert state.lines == 1


def test_last_output_time_only_moves_when_html_arrives() -> None:
    clock = FakeClock()
    reporter = ProgressReporter(now=clock)

    reporter.observe("Here is the guide:")
    reporter.observe("")
    assert snapshot(reporter).last_output_at is None
    assert snapshot(reporter).characters == 0

    reporter.observe("\n```html\n")
    assert snapshot(reporter).last_output_at is None

    reporter.observe("<html>\n")
    first_output = snapshot(reporter).last_output_at
    assert first_output is not None

    reporter.set_activity(ACTIVITY_CHECKING)
    reporter.observe("<p>more document</p>")

    assert snapshot(reporter).last_output_at != first_output


def test_a_new_attempt_resets_the_counters_and_the_label() -> None:
    reporter = ProgressReporter(now=FakeClock())
    reporter.observe("<html>\n<body>first attempt")
    assert snapshot(reporter).lines == 2

    reporter.begin_attempt()

    state = snapshot(reporter)
    assert state.attempt == 1
    assert state.lines == 0
    assert state.characters == 0
    assert state.last_output_at is None

    reporter.observe("<html>\nsecond attempt")
    assert snapshot(reporter).attempt == 1
    assert snapshot(reporter).lines == 2


def test_discarding_a_turn_clears_its_provisional_document() -> None:
    reporter = ProgressReporter(now=FakeClock())
    reporter.begin_attempt()
    reporter.observe("<html>\n<svg></svg>")

    reporter.discard_attempt_progress()

    state = snapshot(reporter)
    assert state.attempt == 1
    assert state.lines == 0
    assert state.characters == 0
    assert state.last_output_at is None


def test_changes_are_reported_once_per_real_change() -> None:
    changes: list[int] = []
    reporter = ProgressReporter(on_change=lambda: changes.append(1), now=FakeClock())

    reporter.set_activity(ACTIVITY_CHECKING)
    reporter.set_activity(ACTIVITY_CHECKING)
    reporter.observe("prose only")
    reporter.observe("<html>\n")

    assert len(changes) == 2


def test_activity_labels_are_shared_with_the_screen() -> None:
    reporter = ProgressReporter(now=FakeClock())

    reporter.set_activity(ACTIVITY_WRITING)

    assert snapshot(reporter).activity == "writing"
