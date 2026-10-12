"""Emitting Qt signals from the worker threads the views schedule.

The task manager runs its jobs on plain daemon threads and calls the callbacks
the views gave it from there. A view that is destroyed while one of its jobs is
still finishing - the window is closed mid-refresh, the test-suite tears a page
down with a task in flight - leaves the callback holding a signal whose C++
source is gone, and PySide6 raises ``RuntimeError`` from ``emit`` instead of
crashing. The emission has nowhere to arrive at that point, so dropping it is
the correct behaviour, not a suppressed error: a traceback printed into the
console at exit is still a defect, and it was a real one.

Every emission that can originate on a worker thread goes through
:func:`emit_if_alive`. Emissions the GUI thread makes itself stay plain ``emit``
calls: a widget emitting its own signal while it is alive cannot fail, and
guarding those would only hide genuine bugs.
"""

from __future__ import annotations

import contextlib

from PySide6.QtCore import SignalInstance

__all__ = ["emit_if_alive"]


def emit_if_alive(signal: SignalInstance, *payload: object) -> None:
    """Emit *signal* with *payload*, or do nothing when its source is gone.

    Called from worker threads, where the widget that owns *signal* can be
    destroyed between the job being scheduled and its result arriving. The
    ``RuntimeError`` PySide6 raises for that case is the signal to stop, not a
    failure to report: there is no receiver left to report it to.
    """
    # "Internal C++ object already deleted" / "Signal source has been deleted":
    # the view this result was for is gone, so the result is dropped with it
    # rather than taking the worker thread down noisily.
    with contextlib.suppress(RuntimeError):
        signal.emit(*payload)
