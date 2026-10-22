"""The elevated half of a Windows run: one command, one log, one exit code.

``pypackhub.core.runner`` starts this file through the OS's own elevation
(SPEC section 11.5) when a confirmed command needs administrator rights on
Windows. The consent boundary carries no pipes, so the *unprivileged*
process cannot read the child's output the way :func:`runner.run` does.
Instead this helper - the process that *is* elevated - starts the approved
command with its stdout and stderr redirected to a log file the
unprivileged process created and follows while the command runs, then
exits with the command's own exit code. The result is that an elevated
install streams its real output: the percentages that move a progress bar
and the error lines that explain a failure.

The rules are the runner's rules. The command arrives as an argument
list and is started as one - no shell is involved anywhere - and the
executable must already be an absolute path the runner resolved, so this
helper never performs a lookup that could be shadowed. It does exactly
one job and has no other surface: a log path and a command on its
command line, nothing else.

The child joins a Windows job object with ``KILL_ON_JOB_CLOSE`` right
after it starts, so the helper's death takes the whole tree with it: a run
the user cancels cannot leave a half-watched install finishing in the
dark. The assignment follows the start rather than preceding it - the
platform would have to suspend the child first for that, and a child
already running when the helper died would outlive the job - so the
guarantee is as strong as that arrangement allows, and the supervisor's
own wait and timeout still apply either way.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

__all__ = ["main"]

#: ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``: the job's processes die when the
#: last handle to the job closes, which is the helper's exit - the only way
#: an unprivileged application can be sure an elevated tree is really gone.
_KILL_ON_JOB_CLOSE: Final = 0x2000

#: ``JobObjectExtendedLimitInformation``, the information class the limit
#: flag above is set through.
_JOB_EXTENDED_LIMIT: Final = 9

#: ``OpenProcess`` rights the job assignment needs: add the process to the
#: job (``PROCESS_SET_QUOTA``) and reserve the right to terminate it.
_PROCESS_SET_QUOTA: Final = 0x0100
_PROCESS_TERMINATE: Final = 0x0001


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``COMMAND`` with its output going to ``LOG``; exit as it did."""

    if os.name != "nt":  # pragma: no cover - Windows is the only caller
        print("elevated_helper is a Windows mechanism", file=sys.stderr)
        return 2

    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) < 2:
        print("usage: elevated_helper LOG COMMAND [ARG ...]", file=sys.stderr)
        return 2

    log_path, command = arguments[0], arguments[1:]

    # The runner resolved the executable before building the elevated line;
    # this side re-resolves nothing, so no lookup here can ever be shadowed.
    if not Path(command[0]).is_absolute():
        print("the elevated command must name an absolute executable", file=sys.stderr)
        return 2

    job = _create_kill_on_close_job()

    # The stack owns the log's lifetime: any return path - including the
    # failure paths below - closes it before the helper exits.
    with contextlib.ExitStack() as stack:
        try:
            log = stack.enter_context(Path(log_path).open("w", encoding="utf-8", newline="\n"))
        except OSError as error:
            print(f"the elevated run could not open its log: {error}", file=sys.stderr)
            return 2

        try:
            process = subprocess.Popen(  # noqa: S603 - argument list, absolute program, no shell
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            log.write(f"the elevated command could not be started: {error}\n")
            return 2

        if job is not None:
            _assign_to_job(job, process)

        process.wait()
        return process.returncode if process.returncode is not None else -1


def _windows_kernel32() -> Any:
    """``ctypes.windll.kernel32``, through ``getattr`` like the runner does.

    The type checker is configured to check this tree as POSIX, so a direct
    ``ctypes.windll`` would be rejected here while being the one thing that
    must run on Windows - the same convention ``core.runner`` follows.
    """
    import ctypes

    windll = getattr(ctypes, "windll")  # noqa: B009 - absent on the POSIX platform
    return windll.kernel32


def _create_kill_on_close_job() -> object | None:
    """A job object whose processes die with it, or ``None`` if unavailable.

    Best effort: if the object cannot be created or configured, the command
    still runs exactly as approved - only the cancel guarantee weakens, and
    the run is supervised by the same wait and timeout as every other.
    """
    import ctypes
    import ctypes.wintypes as wintypes

    kernel32 = _windows_kernel32()

    class IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", ctypes.c_uint),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint),
            ("SchedulingClass", ctypes.c_uint),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    # Every handle is pointer-sized on 64-bit Windows; without these the
    # default C ``int`` conversions would truncate them into invalid
    # handles - the kind of failure that only shows up elevated.
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None

    information = ExtendedLimitInformation()
    information.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        job, _JOB_EXTENDED_LIMIT, ctypes.byref(information), ctypes.sizeof(information)
    ):
        return None

    return job


def _assign_to_job(job: object, process: subprocess.Popen[bytes]) -> None:
    """Put *process* in *job*; best effort, never fatal."""
    import ctypes.wintypes as wintypes

    kernel32 = _windows_kernel32()
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    handle = kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, process.pid)
    if not handle:
        return
    try:
        kernel32.AssignProcessToJobObject(job, handle)
    finally:
        kernel32.CloseHandle(handle)


if __name__ == "__main__":  # pragma: no cover - the elevated entry point
    sys.exit(main())
