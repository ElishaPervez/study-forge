"""Live progress for the current writing attempt.

The panel reports what actually arrived: how many HTML lines and characters were
received for the attempt that is writing right now, and when the last piece
arrived. Counters are held in memory, so receiving more text never rewrites a
guide's saved metadata.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

ACTIVITY_PREPARING = "preparing"
ACTIVITY_WAITING = "waiting"
ACTIVITY_REFERENCES = "references"
ACTIVITY_WRITING = "writing"
ACTIVITY_CHECKING = "checking"
ACTIVITY_CORRECTING = "correcting"
ACTIVITY_RETRYING = "retry"

_HTML_OPEN_RE = re.compile(r"<html[\s>]", re.IGNORECASE)
_HTML_CLOSE_RE = re.compile(r"</html\s*>", re.IGNORECASE)
_DOCTYPE_RE = re.compile(r"<!doctype[^>]*>", re.IGNORECASE)
# Only the tail of the text received before the document opens is kept, so a
# split "<html" is recognized without rescanning the whole accumulated guide.
_PENDING_LIMIT = 256


@dataclass(frozen=True)
class ProgressSnapshot:
    activity: str
    attempt: int
    lines: int
    characters: int
    last_output_at: str | None


def count_document_lines(text: str) -> int:
    """Count received HTML lines, including a final partly written one."""
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


class ProgressReporter:
    def __init__(
        self,
        *,
        on_change: Callable[[], None] | None = None,
        now: Callable[[], str] | None = None,
        activity: str = ACTIVITY_PREPARING,
    ) -> None:
        self._lock = threading.Lock()
        self._on_change = on_change
        self._now = now or _utc_now
        self._activity = activity
        self._attempt = 0
        self._document_open = False
        self._document_closed = False
        self._document = ""
        self._pending = ""
        self._last_output_at: str | None = None

    def set_activity(self, activity: str) -> None:
        with self._lock:
            if self._activity == activity:
                return
            self._activity = activity
        self._notify()

    def begin_attempt(self) -> None:
        """Start a clearly labelled new writing attempt with fresh counters."""
        with self._lock:
            self._attempt += 1
            self._reset_counters()
        self._notify()

    def discard_attempt_progress(self) -> None:
        """Throw away a turn that turned out to be reference work, not writing."""
        with self._lock:
            self._reset_counters()
        self._notify()

    def observe(self, text: str) -> None:
        """Accept one visible piece of the model's reply."""
        if not text:
            return
        changed = False
        with self._lock:
            if self._document_closed:
                return
            if not self._document_open:
                if not self._open_document(text):
                    return
                changed = True
            else:
                self._document += text
                closing = _HTML_CLOSE_RE.search(self._document)
                if closing is not None:
                    self._document = self._document[: closing.end()]
                    self._document_closed = True
                changed = True
            if changed:
                # Real document text arrived, so the activity is writing now.
                self._activity = ACTIVITY_WRITING
                self._last_output_at = self._now()
        self._notify()

    def snapshot(self) -> ProgressSnapshot:
        with self._lock:
            characters = len(self._document)
            return ProgressSnapshot(
                activity=self._activity,
                attempt=self._attempt,
                lines=count_document_lines(self._document),
                characters=characters,
                last_output_at=self._last_output_at,
            )

    def _open_document(self, piece: str) -> bool:
        buffer = self._pending + piece
        opening = _HTML_OPEN_RE.search(buffer)
        if opening is None:
            self._pending = buffer[-_PENDING_LIMIT:] if buffer else ""
            return False
        start = opening.start()
        doctype = None
        for match in _DOCTYPE_RE.finditer(buffer, 0, start):
            doctype = match
        if doctype is not None and not buffer[doctype.end() : start].strip():
            start = doctype.start()
        self._document = buffer[start:]
        self._pending = ""
        self._document_open = True
        closing = _HTML_CLOSE_RE.search(self._document)
        if closing is not None:
            self._document = self._document[: closing.end()]
            self._document_closed = True
        self._last_output_at = self._now()
        return True

    def _reset_counters(self) -> None:
        self._document_open = False
        self._document_closed = False
        self._document = ""
        self._pending = ""
        self._last_output_at = None

    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
