"""What the task manager remembers: the moment, and the caller's context.

The History page reads both: ``timestamp`` puts a clock face on every row,
and ``meta`` is how the packages view says "this was an uninstall of these
names, and here is the snapshot to undo it with" without the history having
to re-parse a command line.
"""

from __future__ import annotations

import threading
import time

from pypackhub.core.runner import CommandResult
from pypackhub.core.tasks import JobState, TaskManager


def _result_payload() -> CommandResult:
    return CommandResult(
        argv=("pip", "install", "--", "left-pad"),
        returncode=0,
        stdout="",
        stderr="",
        duration=0.0,
    )


def _schedule_and_wait(manager: TaskManager, work, *, meta: dict | None = None) -> None:
    """Run *work* and return only once it is in the history.

    ``schedule`` starts a thread; reading the history immediately after is a
    race the thread usually wins only because it does almost nothing. The
    failed-task path does more (the traceback is logged), which is how the
    race was found - so every reader in this file waits for the finish
    callback instead of hoping.
    """
    done = threading.Event()
    manager.schedule("a task", work, meta=meta, on_finish=lambda _id, _result: done.set())
    assert done.wait(timeout=5.0)


def test_a_finished_task_records_its_moment() -> None:
    manager = TaskManager()
    before = time.time()

    _schedule_and_wait(manager, lambda *_args: _result_payload())

    result = manager.history()[0]

    assert result.state is JobState.FINISHED
    assert result.timestamp >= before


def test_the_callers_context_is_recorded_untouched() -> None:
    """``meta`` is the caller's own record; the manager never reads it."""
    manager = TaskManager()
    meta = {"kind": "uninstall", "names": ["left-pad"], "snapshot": "2026-01-01T00-00-00-000"}

    _schedule_and_wait(manager, lambda *_args: _result_payload(), meta=meta)

    recorded = manager.history()[0]
    assert recorded.meta == meta
    # And the recorded meta is a copy: later edits stay out of the history.
    assert recorded.meta is not meta


def test_a_failed_task_is_in_the_history_with_its_error() -> None:
    manager = TaskManager()

    def boom(*_args: object) -> object:
        raise RuntimeError("the index is unreachable")

    _schedule_and_wait(manager, boom)

    result = manager.history()[0]
    assert result.state is JobState.FAILED
    assert "unreachable" in result.error
    assert result.timestamp >= 0.0
