"""Overall progress, read out of a package manager's own output.

Neither pip nor uv reports one trustworthy percentage for a whole run when
their output is piped into another program - the bars they draw on a
terminal are suppressed, which is why a bar waiting for a number to echo
spins forever. What they do print, line by line, is the work itself: every
package resolved, every file fetched, the switch to the install phase,
success. The tracker here maps those real events onto 0-100, so the run's
progress bar fills with what the manager actually did - and only that.

The rules the tracker keeps, all three deliberate:

* it never moves without an event to justify it - no invented curve between
  the lines the manager wrote;
* it never runs backward - a redraw that reports less than an earlier one
  leaves the bar where it was;
* it never claims the last span before the manager announced success, so a
  bar sitting near the top means the work really is nearly done.

The total a run will process is only known up front by uv; for pip the
estimate grows with every package it resolves to fetch, and the phase
boundaries - install, success - are exact wherever the manager reports them.
"""

from __future__ import annotations

import re
from typing import Final

from pypackhub.ui.formatting import parse_progress_percent

__all__ = ["RunProgressTracker"]

#: Resolution and downloads share the span that takes the time. pip's own
#: output announces the phase change past this point; the bar must not claim
#: the boundary a file early.
_WORK_SPAN: Final = 80

#: pip's "Installing collected packages" and uv's "Installed" - everything is
#: fetched and the fast, final phase is running.
_INSTALLING: Final = 95

#: What finishing resolution is worth: uv says exactly when it is done, and
#: the bar should acknowledge real completed work while the first files are
#: still being fetched.
_RESOLVED: Final = 10

#: uv announces the exact package count the moment resolution finishes.
_UV_RESOLVED_COUNT: Final = re.compile(r"^Resolved (\d+) packages?")


class RunProgressTracker:
    """One run's progress, fed one line of manager output at a time.

    Call :meth:`observe` with each line the run streams into the log; it
    returns the new overall percentage when a line moved it, and ``None``
    when the line reported nothing the tracker counts. The value is also
    always readable as :attr:`value`, and it only ever rises.
    """

    def __init__(self) -> None:
        self._value = 0
        #: Packages the run is expected to process: uv's exact count, or
        #: pip's growing estimate from its "Collecting" lines.
        self._total = 0
        self._collected = 0
        #: Files finished - fetched, served from cache, or built.
        self._files_done = 0
        self._current_open = False
        self._fraction = 0.0

    @property
    def value(self) -> int:
        """The highest progress any observed line has justified, 0-100."""
        return self._value

    def observe(self, line: str) -> int | None:
        """Feed one line of output; the new percentage, or ``None``.

        pip and uv are not translated - their output is English on every
        machine whatever the locale - so matching their words is stable.
        """
        text = line.strip()
        if not text:
            return None

        before = self._value

        resolved = _UV_RESOLVED_COUNT.match(text)
        if resolved is not None:
            self._total = max(self._total, int(resolved.group(1)))
            self._raise(_RESOLVED)
        elif text.startswith("Collecting "):
            self._collected += 1
            self._total = max(self._total, self._collected)
            self._recompute()
        elif text.startswith("Downloading "):
            self._start_file()
            self._recompute()
        elif text.startswith("Using cached "):
            # A cached file is processed the moment it is named; the one
            # before it, if any, must be done too, or this line would not
            # have arrived - pip and uv fetch one file at a time.
            self._close_current()
            self._files_done += 1
            self._recompute()
        elif text.startswith("Building wheel") and text.endswith(": started"):
            self._start_file()
            self._recompute()
        elif text.startswith("Building wheel") and text.endswith(": finished"):
            self._close_current()
            self._recompute()
        elif text.startswith("Installing collected packages"):
            self._close_current()
            self._raise(_WORK_SPAN)
        elif text.startswith("Downloaded ") and " package" in text:
            # uv's summary line: every fetch is complete.
            self._close_current()
            self._raise(_WORK_SPAN)
        elif text.startswith("Prepared ") and " package" in text:
            self._close_current()
            self._raise(_WORK_SPAN)
        elif text.startswith("Installed ") and " package" in text:
            self._raise(_INSTALLING)
        elif text.startswith("Successfully installed"):
            self._raise(100)
        else:
            # pip's per-file progress - the same percentage lines the log
            # shows - refines the file currently being fetched. It is a
            # fraction of *one* file, never the whole run.
            percent = parse_progress_percent(text)
            if percent is not None and self._current_open:
                self._fraction = max(self._fraction, percent / 100)
                self._recompute()

        if self._value == before:
            return None
        return self._value

    # -- internals ---------------------------------------------------------

    def _start_file(self) -> None:
        """One file's fetch is starting; the previous one, if any, is done."""
        self._close_current()
        self._current_open = True
        self._fraction = 0.0

    def _close_current(self) -> None:
        """The file being fetched, if any, is finished."""
        if self._current_open:
            self._files_done += 1
        self._current_open = False
        self._fraction = 0.0

    def _recompute(self) -> None:
        """Progress within the work span: files finished plus the current one.

        The total is an estimate for pip, so the value here is an estimate
        too - a monotonic share of what has been seen, sharpened by every
        package pip resolves and corrected exactly at the phase boundaries.
        """
        total = max(self._total, self._files_done, 1)
        processed = self._files_done + self._fraction
        self._raise(min(_WORK_SPAN - 1, int(_WORK_SPAN * processed / total)))

    def _raise(self, value: int) -> None:
        """Move the bar up, never down - a run cannot un-download a file."""
        if value > self._value:
            self._value = value
