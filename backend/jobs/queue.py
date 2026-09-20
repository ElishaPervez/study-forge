"""The single background queue that owns guide work.

One worker runs one request at a time in the order requests were accepted.
Admission (acceptance, rename, delete, and further edits) shares one short lock,
so a change can never slip past a busy guide and run later by surprise.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from backend.generate.progress import (
    ACTIVITY_WAITING,
    ProgressReporter,
    ProgressSnapshot,
)
from backend.jobs.operations import (
    UNFINISHED_STATES,
    current_fingerprint,
    list_operations,
    load_operation,
    new_operation,
    save_operation,
    tombstone_guide_operations,
    update_operation,
)
from backend.jobs.schema import (
    INTERRUPTED_OPERATION_MESSAGE,
    OPERATION_KINDS,
    OperationKind,
    OperationRecord,
)
from backend.jobs.store import load_guide


class QueueError(RuntimeError):
    """A request the queue refuses to accept."""

    status_code = 409


class GuideBusyError(QueueError):
    pass


class DuplicateRequestError(QueueError):
    pass


class ShutdownInProgressError(QueueError):
    pass


class DeletedRequestError(QueueError):
    pass


class VersionChangedError(QueueError):
    pass


class UnknownRequestError(QueueError):
    status_code = 404


@dataclass(frozen=True)
class QueueRequest:
    guide_id: str
    kind: OperationKind
    receipt: str | None = None
    selected_text: str | None = None
    instruction: str | None = None
    revision_mode: str | None = None
    intended_revision: int = 0
    base_fingerprint: str | None = None
    # Runs under the admission lock before the request record is saved, so an
    # interrupted acceptance cannot create a second guide on a repeated receipt.
    prepare: Callable[[], None] | None = None


@dataclass(frozen=True)
class RunOutcome:
    ok: bool
    error: str | None = None
    guide_id: str | None = None


@dataclass
class OperationContext:
    request: OperationRecord
    progress: ProgressReporter
    stop: threading.Event
    queue: GuideQueue

    @property
    def stopped(self) -> bool:
        return self.stop.is_set()


Runner = Callable[[OperationRecord, OperationContext], RunOutcome]


class GuideQueue:
    def __init__(
        self,
        *,
        jobs_dir: Path,
        runner: Runner,
        service_start: str | None = None,
    ) -> None:
        self.jobs_dir = jobs_dir
        self.service_start = service_start or uuid.uuid4().hex
        self._runner = runner
        # One lock owns queue state, so acceptance, mutations, and worker
        # transitions can never observe a half-updated picture.
        self._condition = threading.Condition()
        self._operations: dict[str, OperationRecord] = {}
        self._pending: list[str] = []
        self._live: dict[str, OperationContext] = {}
        self._busy: set[str] = set()
        self._guide_names: dict[str, str] = {}
        self._session_completions: set[str] = set()
        self._next_order = 1
        self._change = 0
        self._accepting = True
        self._advancing = True
        self._shutdown = False
        self._stopped = False
        self._worker: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        with self._condition:
            if self._worker is not None or self._stopped:
                return
            self._load_stored_operations()
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="study-forge-queue",
                daemon=True,
            )
            self._worker.start()

    def stop(self, timeout: float | None = 5.0) -> None:
        with self._condition:
            self._stopped = True
            for context in self._live.values():
                context.stop.set()
            self._condition.notify_all()
            worker = self._worker
        if worker is not None:
            worker.join(timeout)

    @property
    def worker_alive(self) -> bool:
        worker = self._worker
        return worker is not None and worker.is_alive()

    # -- admission ---------------------------------------------------------

    @contextmanager
    def admission(self) -> Iterator[None]:
        """The short lock every guide-changing action shares."""
        with self._condition:
            yield

    @contextmanager
    def publication(self) -> Iterator[bool]:
        """Brief admission that decides whether a checked result may still publish."""
        with self._condition:
            yield not self._shutdown

    def guide_is_busy(self, guide_id: str) -> bool:
        with self._condition:
            return guide_id in self._busy

    def busy_guides(self) -> frozenset[str]:
        with self._condition:
            return frozenset(self._busy)

    def remember_guide(self, guide_id: str, name: str) -> None:
        with self._condition:
            self._guide_names[guide_id] = name

    def mark_guide_deleted(self, guide_id: str) -> None:
        """Keep only receipt tombstones after a guide is deleted."""
        with self._condition:
            tombstone_guide_operations(self.jobs_dir, guide_id)
            self._guide_names.pop(guide_id, None)
            self._busy.discard(guide_id)
            for receipt in [
                receipt
                for receipt, operation in self._operations.items()
                if operation.guide_id == guide_id
            ]:
                if receipt in self._pending:
                    self._pending.remove(receipt)
                self._live.pop(receipt, None)
            self._load_stored_operations()
            self._bump()

    # -- acceptance --------------------------------------------------------

    def submit(self, request: QueueRequest) -> OperationRecord:
        with self._condition:
            existing = self._existing_request(request)
            if existing is not None:
                return existing

            if not self._accepting:
                raise ShutdownInProgressError(
                    "the app is closing, so new requests are not accepted"
                )
            if request.kind not in OPERATION_KINDS:
                raise QueueError(f"unknown request kind: {request.kind!r}")
            if request.guide_id in self._busy or self._stored_active(request.guide_id):
                raise GuideBusyError("this guide already has a request waiting or running")
            if request.base_fingerprint is not None:
                self._require_current_version(request)

            if request.prepare is not None:
                request.prepare()

            operation = new_operation(
                guide_id=request.guide_id,
                kind=request.kind,
                order=self._next_order,
                receipt=request.receipt,
                selected_text=request.selected_text,
                instruction=request.instruction,
                revision_mode=request.revision_mode,
                intended_revision=request.intended_revision,
                base_fingerprint=request.base_fingerprint,
            )
            save_operation(self.jobs_dir, operation)
            self._next_order += 1
            self._operations[operation.receipt] = operation
            self._pending.append(operation.receipt)
            self._busy.add(operation.guide_id)
            self._live[operation.receipt] = self._new_context(operation)
            self._bump()
            self._condition.notify_all()
            return operation

    def retry_request(self, receipt: str, *, new_receipt: str | None = None) -> OperationRecord:
        with self._condition:
            original = self.operation(receipt)
            if original is None or original.tombstone:
                raise UnknownRequestError("unknown request")
            if not original.retry_available:
                raise QueueError("that request is not retryable")
            kind = "retry" if original.kind in {"create", "retry"} else original.kind
            return self.submit(
                QueueRequest(
                    guide_id=original.guide_id,
                    kind=kind,
                    receipt=new_receipt,
                    selected_text=original.selected_text,
                    instruction=original.instruction,
                    revision_mode=original.revision_mode,
                    intended_revision=original.intended_revision,
                    base_fingerprint=original.base_fingerprint,
                )
            )

    def operation(self, receipt: str) -> OperationRecord | None:
        with self._condition:
            stored = self._operations.get(receipt)
            if stored is None:
                stored = self._reload_operation(receipt)
            return stored

    def row(self, receipt: str) -> dict | None:
        """The queue-facing view of one request, or None when it is unknown."""
        with self._condition:
            operation = self._operations.get(receipt)
            if operation is None:
                operation = self._reload_operation(receipt)
            if operation is None or operation.tombstone:
                return None
            return self._row(operation)

    def guide_operations(self, guide_id: str) -> list[OperationRecord]:
        return [
            operation
            for operation in list_operations(self.jobs_dir)
            if operation.guide_id == guide_id
        ]

    def current_operation(self, guide_id: str) -> OperationRecord | None:
        """The request the screen should follow for this guide right now."""
        with self._condition:
            own = [
                operation
                for operation in self._operations.values()
                if operation.guide_id == guide_id and not operation.tombstone
            ]
        active = [operation for operation in own if operation.is_active]
        if active:
            running = [operation for operation in active if operation.state == "running"]
            return (running or active)[0]
        return None

    def retryable_failure(self, guide_id: str) -> OperationRecord | None:
        """The newest failed or interrupted request that can still be retried."""
        with self._condition:
            candidates = [
                operation
                for operation in self._operations.values()
                if operation.guide_id == guide_id
                and not operation.tombstone
                and operation.state in {"failed", "interrupted"}
            ]
        if not candidates:
            return None
        newest = max(candidates, key=lambda operation: (operation.order, operation.created_at))
        if newest.base_fingerprint is not None and not self._version_still_matches(newest):
            return None
        return newest

    # -- queue information -------------------------------------------------

    def snapshot(self) -> dict:
        with self._condition:
            rows = [self._row(operation) for operation in self._visible_operations()]
            return {
                "service_start": self.service_start,
                "change_number": self._change,
                "accepting": self._accepting and not self._shutdown,
                "closing": not self._accepting,
                "operations": rows,
            }

    def change_number(self) -> int:
        with self._condition:
            return self._change

    def close_status(self) -> dict:
        with self._condition:
            active = sum(1 for operation in self._operations.values() if operation.state == "running")
            waiting = sum(1 for operation in self._operations.values() if operation.state == "waiting")
            return {
                "active": active,
                "waiting": waiting,
                "accepting": self._accepting and not self._shutdown,
                "confirmed": self._shutdown,
            }

    # -- close decisions ---------------------------------------------------

    def prepare_close(self) -> dict:
        with self._condition:
            self._accepting = False
            self._advancing = False
            self._bump()
            return self.close_status()

    def resume_close(self) -> dict:
        with self._condition:
            if self._shutdown:
                return self.close_status()
            self._accepting = True
            self._advancing = True
            self._bump()
            self._condition.notify_all()
            return self.close_status()

    def confirm_close(self) -> dict:
        with self._condition:
            interrupted = self.close_status()
            self._shutdown = True
            self._accepting = False
            self._advancing = False
            for receipt in list(self._pending):
                operation = self._operations.get(receipt)
                if operation is None or operation.state != "waiting":
                    continue
                self._interrupt(operation)
                self._pending.remove(receipt)
                self._release_busy(operation)
            for context in self._live.values():
                context.stop.set()
            self._bump()
            self._condition.notify_all()
            return {**interrupted, "accepting": False, "confirmed": True}

    # -- worker ------------------------------------------------------------

    def _worker_loop(self) -> None:
        while True:
            request = self._next_request()
            if request is None:
                return
            self._execute(request)

    def _next_request(self) -> OperationRecord | None:
        with self._condition:
            while True:
                if self._stopped or self._shutdown:
                    return None
                if self._pending and self._advancing:
                    receipt = self._pending[0]
                    operation = self._operations.get(receipt)
                    if operation is None:
                        self._pending.pop(0)
                        continue
                    operation = update_operation(
                        self.jobs_dir, operation, state="running", started_at=_now()
                    )
                    self._operations[receipt] = operation
                    self._pending.pop(0)
                    self._bump()
                    return operation
                self._condition.wait()

    def _execute(self, request: OperationRecord) -> None:
        context = self._live.get(request.receipt)
        if context is None:
            context = self._new_context(request)
            self._live[request.receipt] = context
        context.progress.set_activity(ACTIVITY_WAITING)
        try:
            outcome = self._runner(request, context)
        except Exception as error:  # noqa: BLE001 - the queue must survive any runner failure
            outcome = RunOutcome(False, str(error) or error.__class__.__name__)
        if outcome is None:
            outcome = RunOutcome(False, "the request ended without a result")
        with self._condition:
            self._finish(request, outcome)
            self._bump()
            self._condition.notify_all()

    def _finish(self, request: OperationRecord, outcome: RunOutcome) -> None:
        operation = self._operations.get(request.receipt, request)
        if self._shutdown:
            operation = self._interrupt(operation)
        elif outcome.ok:
            operation = update_operation(
                self.jobs_dir,
                operation,
                state="completed",
                finished_at=_now(),
                error=None,
            )
            self._session_completions.add(operation.receipt)
        else:
            operation = update_operation(
                self.jobs_dir,
                operation,
                state="failed",
                finished_at=_now(),
                error=outcome.error or "the request failed",
            )
        self._operations[operation.receipt] = operation
        self._live.pop(operation.receipt, None)
        self._release_busy(operation)

    def _release_busy(self, operation: OperationRecord) -> None:
        if self._stored_active(operation.guide_id) or any(
            other.is_active and other.guide_id == operation.guide_id
            for other in self._operations.values()
        ):
            return
        self._busy.discard(operation.guide_id)

    def _interrupt(self, operation: OperationRecord) -> OperationRecord:
        interrupted = update_operation(
            self.jobs_dir,
            operation,
            state="interrupted",
            error=INTERRUPTED_OPERATION_MESSAGE,
            finished_at=_now(),
        )
        self._operations[interrupted.receipt] = interrupted
        return interrupted

    # -- internals ---------------------------------------------------------

    def _existing_request(self, request: QueueRequest) -> OperationRecord | None:
        if request.receipt is None:
            return None
        existing = self.operation(request.receipt)
        if existing is None:
            return None
        if existing.tombstone:
            raise DeletedRequestError("that request belongs to a deleted guide")
        if (
            existing.guide_id != request.guide_id
            or existing.kind != request.kind
            or existing.selected_text != request.selected_text
            or existing.instruction != request.instruction
            or existing.revision_mode != request.revision_mode
            or existing.intended_revision != request.intended_revision
            or existing.base_fingerprint != request.base_fingerprint
        ):
            raise DuplicateRequestError("that receipt was already used for different content")
        return existing

    def _require_current_version(self, request: QueueRequest) -> None:
        guide = self._load_guide(request.guide_id)
        if guide is None:
            raise UnknownRequestError("unknown guide")
        if current_fingerprint(self.jobs_dir, guide) != request.base_fingerprint:
            raise VersionChangedError(
                "this guide changed since the update was requested; start a fresh edit"
            )

    def _stored_active(self, guide_id: str) -> bool:
        return any(
            operation.is_active and operation.guide_id == guide_id
            for operation in self._operations.values()
        )

    def _visible_operations(self) -> list[OperationRecord]:
        visible = [
            operation
            for operation in self._operations.values()
            if operation.tombstone is False
            and (
                operation.state in UNFINISHED_STATES | {"failed", "interrupted"}
                or operation.receipt in self._session_completions
            )
        ]
        return sorted(visible, key=lambda operation: (operation.order, operation.created_at))

    def _row(self, operation: OperationRecord) -> dict:
        row = operation.summary()
        row["guide_name"] = self._guide_name(operation.guide_id)
        context = self._live.get(operation.receipt)
        progress: ProgressSnapshot | None = context.progress.snapshot() if context else None
        row.update(
            {
                "activity": progress.activity if progress else None,
                "attempt": progress.attempt if progress else 0,
                "lines": progress.lines if progress else 0,
                "characters": progress.characters if progress else 0,
                "last_output_at": progress.last_output_at if progress else None,
            }
        )
        if operation.base_fingerprint is not None and operation.retry_available:
            row["retry_available"] = self._version_still_matches(operation)
        return row

    def _version_still_matches(self, operation: OperationRecord) -> bool:
        guide = self._load_guide(operation.guide_id)
        if guide is None:
            return False
        return current_fingerprint(self.jobs_dir, guide) == operation.base_fingerprint

    def _guide_name(self, guide_id: str) -> str:
        cached = self._guide_names.get(guide_id)
        if cached is not None:
            return cached
        guide = self._load_guide(guide_id)
        name = guide.name if guide is not None else "Untitled guide"
        self._guide_names[guide_id] = name
        return name

    def _load_guide(self, guide_id: str):
        try:
            return load_guide(self.jobs_dir, guide_id)
        except (OSError, TypeError, ValueError):
            return None

    def _reload_operation(self, receipt: str) -> OperationRecord | None:
        try:
            stored = load_operation(self.jobs_dir, receipt)
        except (OSError, TypeError, ValueError):
            return None
        self._operations[receipt] = stored
        return stored

    def _load_stored_operations(self) -> None:
        for operation in list_operations(self.jobs_dir):
            self._operations[operation.receipt] = operation
        orders = [operation.order for operation in self._operations.values()]
        self._next_order = max(orders, default=0) + 1

    def refresh_operations(self) -> None:
        """Re-read saved requests after an external change such as a deletion."""
        with self._condition:
            self._load_stored_operations()

    def _new_context(self, operation: OperationRecord) -> OperationContext:
        return OperationContext(
            request=operation,
            progress=ProgressReporter(on_change=self._bump),
            stop=threading.Event(),
            queue=self,
        )

    def _bump(self) -> None:
        with self._condition:
            self._change += 1


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")
