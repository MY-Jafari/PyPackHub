"""Job queue, cancellation and progress for PyPackHub's core.

A task here is a first-class, cancel cancellable unit of work with a stable id,
a human label, a progress range and a history entry when it finishes. Backends
and the UI both talk to this layer rather than spawning freestanding threads.

Nothing in this module knows about Qt. The worker threads it owns are plain
threads that report back through callbacks and futures so the core stays usable
by the CLI companion and the test suite.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pypackhub.core.runner import CommandResult

__all__ = [
    "JobState",
    "TaskCancelled",
    "TaskCancelledError",
    "TaskError",
    "TaskManager",
    "TaskProgress",
    "TaskResult",
]

_LOGGER = logging.getLogger(__name__)


class JobState(str, Enum):
    """Where a task is in its lifecycle."""

    PENDING = "pending"
    RUNNING = "running"
    CANCELLED = "cancelled"
    FINISHED = "finished"
    FAILED = "failed"


@dataclass(slots=True)
class TaskProgress:
    """A snapshot of how far a task has come."""

    current: int = 0
    total: int = 1
    text: str = ""

    @property
    def fraction(self) -> float:
        if self.total <= 0:
            return 1.0
        return max(0.0, min(1.0, self.current / self.total))


@dataclass(slots=True)
class TaskResult:
    """What a task produced when it finished."""

    state: JobState
    progress: TaskProgress
    payload: Any = None
    error: str = ""
    #: When the task finished (``time.time()``). Zero until then, which is how
    #: a caller can tell "not finished" from "finished long ago".
    timestamp: float = 0.0
    #: The id of the task this result belongs to, so a history entry can be
    #: correlated back to the task that produced it.
    task_id: str = ""
    #: Caller-supplied context for the history: what the task was about, beyond
    #: its label. The packages view fills this in for changing actions - the
    #: action kind, the names it touched, the snapshot taken before it - so
    #: the history page can offer an undo without re-parsing command lines.
    meta: dict[str, Any] = dataclasses.field(default_factory=dict)


class TaskCancelledError(Exception):
    """Raised inside a task callback when the user asked to stop it."""


TaskCancelled = TaskCancelledError


class TaskError(RuntimeError):
    """A task failed for a reason the manager should record as a history entry."""


class TaskManager:
    """A small in-process task manager with cancellation and a linear history.

    This is the section 2 skeleton. It owns its own worker thread, assigns ids,
    routes progress and completion back to whoever asked for it, and keeps a
    bounded history so the UI can show what ran recently without asking the
    backend for it.
    """

    def __init__(self, *, history_limit: int = 64) -> None:
        self._history_limit = history_limit
        self._history: list[TaskResult] = []
        self._tasks: dict[str, threading.Thread] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.RLock()
        self._next_id = 1

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def schedule(
        self,
        label: str,
        work: Callable[[str, TaskProgress, threading.Event], Any],
        *,
        on_progress: Callable[[str, TaskProgress], None] | None = None,
        on_finish: Callable[[str, TaskResult], None] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        """Schedule a task and return its id.

        ``work`` is run on a background thread. It may call the cancellable
        helper :func:`wait_for_cancel` or read the event passed to it directly.
        ``on_progress`` and ``on_finish`` are called on that same thread, so
        they must be quick or shunt work to the main thread themselves.
        ``meta`` is recorded on the result untouched, for the caller's own
        history - the task manager never reads it.
        """
        # The id is minted under the lock: two threads scheduling at once
        # would otherwise race the counter and hand out the same id.
        with self._lock:
            task_id = f"task-{self._next_id}"
            self._next_id += 1

        cancel = threading.Event()
        state: JobState = JobState.PENDING
        progress = TaskProgress()
        recorded_meta = dict(meta) if meta else {}

        def runner() -> None:
            nonlocal state, progress

            try:
                state = JobState.RUNNING
                payload = work(task_id, progress, cancel)

                state = JobState.FINISHED
                result = TaskResult(
                    state=state,
                    progress=progress,
                    payload=payload,
                    timestamp=time.time(),
                    meta=recorded_meta,
                    task_id=task_id,
                )
            except TaskCancelled:
                state = JobState.CANCELLED
                result = TaskResult(
                    state=state,
                    progress=progress,
                    error="cancelled",
                    timestamp=time.time(),
                    meta=recorded_meta,
                    task_id=task_id,
                )
            except Exception as error:
                # The failure is recorded for the UI either way, but a
                # programming bug (a TypeError in a work closure) is
                # indistinguishable from an expected failure unless the
                # traceback is logged here.
                _LOGGER.exception("task %s failed", task_id)
                state = JobState.FAILED
                result = TaskResult(
                    state=state,
                    progress=progress,
                    error=str(error),
                    timestamp=time.time(),
                    meta=recorded_meta,
                    task_id=task_id,
                )

            with self._lock:
                self._append_history(result)
                # A finished task has no cancel event left to set and no
                # thread left to join; keeping either would grow one entry
                # per task for the process lifetime.
                self._cancel.pop(task_id, None)
                self._tasks.pop(task_id, None)

            if on_finish is not None:
                on_finish(task_id, result)

        thread = threading.Thread(
            target=runner,
            name=f"pypackhub-task-{task_id}",
            daemon=True,
        )

        with self._lock:
            # The id is minted under the same lock that registers the task:
            # two threads scheduling at once would otherwise race the
            # counter and hand out the same id.
            self._register(task_id, cancel, thread)

        thread.start()

        return task_id

    def _register(self, task_id: str, cancel: threading.Event, thread: threading.Thread) -> None:
        self._cancel[task_id] = cancel
        self._tasks[task_id] = thread

    # ------------------------------------------------------------------
    # Cancellation and state
    # ------------------------------------------------------------------

    def cancel(self, task_id: str) -> bool:
        """Ask a task to stop, if it is still cancellable."""
        with self._lock:
            cancel = self._cancel.get(task_id)

        if cancel is None:
            return False

        cancel.set()
        return True

    def cancel_all(self) -> int:
        """Cancel every currently running task."""
        with self._lock:
            pending = list(self._cancel.values())

        # ``threading.Event.set()`` returns None, so the count comes from the
        # tasks that were actually cancelled, not from the return value.
        cancelled = 0
        for cancel in pending:
            if not cancel.is_set():
                cancel.set()
                cancelled += 1

        return cancelled

    def shutdown(self, timeout: float = 3.0) -> None:
        """Ask every running task to stop, then wait for the workers to exit.

        Called when the window closes: the worker threads are daemon threads,
        so without this a ``pip install`` in flight would be orphaned mid-run -
        the child process would keep going with no timeout and no supervision
        left to watch it. *timeout* bounds the wait so a wedged task cannot
        keep the window open indefinitely.
        """
        self.cancel_all()
        with self._lock:
            threads = list(self._tasks.values())

        deadline = time.monotonic() + max(0.0, timeout)
        for thread in threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))

    def state(self, task_id: str) -> JobState | None:
        """Best-effort current state. The authoritative source is the finish callback."""
        with self._lock:
            thread = self._tasks.get(task_id)

        if thread is None:
            return None

        if not thread.is_alive():
            last = self._last_history_for(task_id)
            if last is not None:
                return last.state

        return JobState.RUNNING

    def history(self) -> list[TaskResult]:
        with self._lock:
            return list(self._history)

    # ------------------------------------------------------------------
    # Helpers for task authors
    # ------------------------------------------------------------------

    @staticmethod
    def wait_for_cancel(cancel: threading.Event, interval: float = 0.1) -> None:
        """Sleep until ``cancel`` is set or the interval expires."""
        while not cancel.wait(interval):
            pass

    @staticmethod
    def from_command_result(result: CommandResult) -> TaskResult:
        """Convert a runner result into a task result for history."""
        if result.cancelled:
            state: JobState = JobState.CANCELLED
            error = "command was cancelled"
        elif result.timed_out:
            state = JobState.FAILED
            error = "command timed out"
        elif result.returncode != 0:
            state = JobState.FAILED
            error = f"command failed with exit code {result.returncode}"
        else:
            state = JobState.FINISHED
            error = ""

        return TaskResult(
            state=state,
            progress=TaskProgress(total=1, current=1),
            payload=result,
            error=error,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _append_history(self, result: TaskResult) -> None:
        self._history.append(result)
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit :]

    def _last_history_for(self, task_id: str) -> TaskResult | None:
        """The recorded result of one task, matched on the id it carried."""
        with self._lock:
            for entry in reversed(self._history):
                if entry.task_id == task_id and getattr(entry.payload, "argv", None) is None:
                    return entry
        return None
