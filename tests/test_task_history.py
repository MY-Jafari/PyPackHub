"""What the task manager remembers: the moment, and the caller's context.

The History page reads both: ``timestamp`` puts a clock face on every row,
and ``meta`` is how the packages view says "this was an uninstall of these
names, and here is the snapshot to undo it with" without the history having
to re-parse a command line.
"""

from __future__ import annotations

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


def test_a_finished_task_records_its_moment() -> None:
    manager = TaskManager()
    before = time.time()

    finished: list[object] = []
    manager.schedule(
        "a task", lambda *_args: _result_payload(), on_finish=lambda _id, r: finished.append(r)
    )

    result = manager.history()[0]

    assert result.state is JobState.FINISHED
    assert result.timestamp >= before


def test_the_callers_context_is_recorded_untouched() -> None:
    """``meta`` is the caller's own record; the manager never reads it."""
    manager = TaskManager()
    meta = {"kind": "uninstall", "names": ["left-pad"], "snapshot": "2026-01-01T00-00-00-000"}

    manager.schedule(
        "pip uninstall",
        lambda *_args: _result_payload(),
        meta=meta,
        on_finish=lambda _id, _r: None,
    )

    assert manager.history()[0].meta == meta
    # And the recorded meta is a copy: later edits stay out of the history.
    assert manager.history()[0].meta is not meta


def test_a_failed_task_is_in_the_history_with_its_error() -> None:
    manager = TaskManager()

    def boom(*_args: object) -> object:
        raise RuntimeError("the index is unreachable")

    manager.schedule("a failing task", boom, on_finish=lambda _id, _r: None)

    result = manager.history()[0]
    assert result.state is JobState.FAILED
    assert "unreachable" in result.error
    assert result.timestamp >= 0.0
