"""Hardened process execution - the only place in PyPackHub that spawns a process.

Every subprocess in the application goes through :func:`run`. The guarantees
this module provides (SPEC section 11.1):

* an argument list, never a command string. There is no ``shell=True`` code
  path and no parameter that enables one;
* executables are resolved explicitly. A bare name is looked up through a
  hardened search path; a relative path is refused outright, so a file planted
  in a project folder can never be picked up by accident;
* the child gets a sanitized environment built from an explicit allow-list.
  ``PYTHONPATH``/``PYTHONHOME``/``VIRTUAL_ENV`` and every ``PIP_*``/``UV_*``
  variable are stripped, because ``PYTHONPATH`` can shadow the package
  manager's own modules and the others can silently redirect an install to a
  different index;
* every child gets its own process group, so cancelling terminates the whole
  tree rather than just the parent;
* timeouts are enforced, and partial output survives both a timeout and a
  cancel, so the UI always has something to show.

``scripts/security_scan.py`` fails the build if any other module in the project
imports :mod:`subprocess`, calls ``os.system``/``os.popen``, or passes
``shell=True``. That scanner, not reviewer discipline, is what keeps this the
only spawn site.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final

from pypackhub.core.platform_guard import is_windows_app_alias_stub
from pypackhub.core.secrets import redact

__all__ = [
    "DEFAULT_TIMEOUT",
    "CommandFailedError",
    "CommandResult",
    "ExecutableNotFoundError",
    "InvalidCommandError",
    "RunnerError",
    "elevation_supported",
    "format_argument",
    "resolve_executable",
    "run",
    "run_elevated",
    "safe_search_path",
    "sanitized_environment",
    "terminate_process_tree",
]

#: Default wall-clock limit for a child process. Long-running operations may
#: pass a larger value or ``None`` explicitly, but nothing may run forever by
#: accident.
DEFAULT_TIMEOUT: Final = 600.0

#: How long a process gets to exit after being asked politely, before it is
#: killed outright.
GRACE_PERIOD: Final = 5.0

#: How often the wait loop checks for completion, cancellation and timeout.
_POLL_INTERVAL: Final = 0.05

_ENV_ALLOWLIST_POSIX: Final = frozenset(
    {
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LC_MESSAGES",
        "LOGNAME",
        "HOME",
        "PATH",
        "SHELL",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TERM",
        "TMPDIR",
        "TZ",
        "USER",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR",
    }
)

_ENV_ALLOWLIST_WINDOWS: Final = frozenset(
    {
        "ALLUSERSPROFILE",
        "APPDATA",
        "COMSPEC",
        "HOMEDRIVE",
        "HOMEPATH",
        "LANG",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_IDENTIFIER",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERDOMAIN",
        "USERNAME",
        "USERPROFILE",
        "WINDIR",
    }
)

#: Stripped even if a future edit adds one of them to an allow-list. Every name
#: here can change which code a child interpreter loads, or which index a
#: package manager talks to.
_ALWAYS_STRIPPED: Final = frozenset(
    {
        "PIP_CONFIG_FILE",
        "PYTHONEXECUTABLE",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONUSERBASE",
        "VIRTUAL_ENV",
    }
)

#: Variable prefixes that are never inherited, for the same reason.
_ALWAYS_STRIPPED_PREFIXES: Final = ("PIP_", "UV_", "POETRY_", "CONDA_")

#: Applied after the allow-list, on top of whatever the caller passes in. These
#: make child output line-buffered, UTF-8 and non-interactive, which is what a
#: GUI needs to show a live log and to never hang on a prompt.
_BASELINE_ENV: Final = {
    "NO_COLOR": "1",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "PIP_NO_COLOR": "1",
    "PIP_NO_INPUT": "1",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUNBUFFERED": "1",
}

_QUOTING_PATTERN: Final = re.compile(r"""[\s"'\\$`|&;<>()*?{}\[\]~!#^=]""")
_PATH_SEPARATORS: Final = ("/", "\\")


class RunnerError(RuntimeError):
    """Base class for every failure raised by this module."""


class InvalidCommandError(RunnerError):
    """The command itself is malformed or refused by policy."""


class ExecutableNotFoundError(RunnerError):
    """The requested program could not be resolved to a real executable."""


class CommandFailedError(RunnerError):
    """Raised by :func:`run` with ``check=True`` when a command did not succeed."""

    def __init__(self, result: CommandResult) -> None:
        super().__init__(
            f"command failed with exit code {result.returncode}: {result.display_command()}"
        )
        self.result = result


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Everything the caller needs to explain what happened, and why."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.cancelled

    @property
    def output(self) -> str:
        """stdout and stderr combined, for the log panel."""

        if not self.stderr:
            return self.stdout
        if not self.stdout:
            return self.stderr

        return f"{self.stdout}\n{self.stderr}"

    def display_command(self) -> str:
        """Render the command for a log line or a confirmation dialog.

        Redacted and derived from :attr:`argv` itself, so the text a user
        confirms is always the text that was executed - never a re-rendering
        that could drift from it.
        """

        return redact(" ".join(format_argument(argument) for argument in self.argv))


def format_argument(argument: str) -> str:
    """Quote one argument for display only.

    This is deliberately not a shell escaping routine: nothing here is ever
    handed to a shell. The goal is a faithful, readable rendering of the exact
    argument list that was executed.
    """

    if argument and not _QUOTING_PATTERN.search(argument):
        return argument

    return '"' + argument.replace("\\", "\\\\").replace('"', '\\"') + '"'


def safe_search_path(
    path: str | None = None,
    *,
    lookup_path: str | None = None,
) -> str:
    """Return the search path with relative and empty entries removed.

    A POSIX ``PATH`` treats an empty entry as "the current directory", and a
    relative entry is just as dangerous: both let a file that happens to sit in
    whatever folder the user opened shadow a real tool.
    """

    raw = os.environ.get("PATH", "") if lookup_path is None else lookup_path
    separator = os.pathsep if path is None else path
    entries = [entry for entry in raw.split(separator) if entry and Path(entry).is_absolute()]

    if not entries:
        # Falling back to an empty search path would silently break every tool
        # lookup; an empty string keeps the failure explicit and local.
        return ""

    return separator.join(entries)


def sanitized_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build the environment a child process is allowed to see.

    Built from an allow-list rather than by removing known-bad names: a
    variable nobody thought of is absent by default instead of inherited by
    accident. ``extra`` is trusted input from our own code and is applied last.
    """

    allowlist = _ENV_ALLOWLIST_WINDOWS if os.name == "nt" else _ENV_ALLOWLIST_POSIX

    environment: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper not in allowlist:
            continue
        if upper in _ALWAYS_STRIPPED or upper.startswith(_ALWAYS_STRIPPED_PREFIXES):
            continue
        environment[key] = value

    environment["PATH"] = safe_search_path()
    environment.update(_BASELINE_ENV)

    if extra:
        for key, value in extra.items():
            if "\x00" in key or "\x00" in value:
                raise InvalidCommandError(f"environment variable {key!r} contains a NUL byte")
            environment[key] = value

    return environment


def resolve_executable(name: str, *, search_path: str | None = None) -> str:
    """Resolve a program name to an absolute path, or refuse.

    A value containing a path separator is rejected rather than resolved:
    ``./pip`` or ``../scripts/pip`` must never be executable through this API.
    Callers that mean a specific file pass its absolute path.
    """

    if not name:
        raise InvalidCommandError("executable name is empty")

    if name.startswith("-"):
        raise InvalidCommandError(f"executable name may not start with '-': {name!r}")

    # An absolute path is already unambiguous and is used as given. Everything
    # else has to be a bare name: a relative path would resolve against the
    # current working directory, which is exactly the shadowing we refuse.
    if Path(name).is_absolute():
        if not Path(name).is_file():
            raise ExecutableNotFoundError(f"{name!r} does not exist")
        if is_windows_app_alias_stub(Path(name)):
            raise ExecutableNotFoundError(
                f"{name!r} is a Windows App Execution Alias stub, not a real executable"
            )
        return name

    if any(separator in name for separator in _PATH_SEPARATORS):
        raise InvalidCommandError(
            f"{name!r} looks like a path; pass an absolute path or a bare program name"
        )

    resolved = shutil.which(
        name, path=search_path if search_path is not None else safe_search_path()
    )

    if resolved is None:
        raise ExecutableNotFoundError(f"could not find {name!r} on the search path")

    if is_windows_app_alias_stub(Path(resolved)):
        raise ExecutableNotFoundError(
            f"{resolved!r} is a Windows App Execution Alias stub, not a real executable"
        )

    return resolved


def validate_argv(argv: Sequence[str]) -> tuple[str, ...]:
    """Normalise and sanity-check an argument list before it is executed."""

    arguments = tuple(argv)

    if not arguments:
        raise InvalidCommandError("argument list is empty")

    for index, argument in enumerate(arguments):
        if not isinstance(argument, str):  # pragma: no cover - defensive, typed callers
            raise InvalidCommandError(f"argument {index} is not a string")
        if "\x00" in argument:
            raise InvalidCommandError(f"argument {index} contains a NUL byte")
        if index == 0 and not argument:
            raise InvalidCommandError("program name is empty")

    return arguments


def terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Terminate a process and everything it spawned."""

    if process.poll() is not None:
        return

    if os.name == "nt":
        _terminate_windows_tree(process)
    else:
        _terminate_posix_tree(process)


def _terminate_posix_tree(process: subprocess.Popen[str]) -> None:
    try:
        group = os.getpgid(process.pid)
    except (ProcessLookupError, PermissionError):  # pragma: no cover - race on exit
        process.kill()
        return

    try:
        os.killpg(group, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):  # pragma: no cover - race on exit
        process.kill()
        return

    try:
        process.wait(timeout=GRACE_PERIOD)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        os.killpg(group, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # pragma: no cover - race on exit
        process.kill()


def _terminate_windows_tree(process: subprocess.Popen[str]) -> None:
    # `taskkill /T` is the only reliable way to take down a whole tree on
    # Windows; the console's own job objects are not available from Python.
    taskkill = shutil.which("taskkill")

    if taskkill is not None:
        # Best effort: if the tree is already gone, that is the outcome we
        # wanted, and the wait below is what reports anything that went wrong.
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(  # noqa: S603 - fixed argument list, resolved absolute path
                [taskkill, "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=GRACE_PERIOD,
                check=False,
            )

    try:
        process.wait(timeout=GRACE_PERIOD)
    except subprocess.TimeoutExpired:  # pragma: no cover - best effort
        process.kill()


def _start_reader(
    stream: IO[str] | None,
    sink: list[str],
    callback: Callable[[str], None] | None,
) -> threading.Thread:
    """Drain one of the child's pipes on its own thread.

    The pipe must be drained whether or not the caller wants a callback:
    a full pipe buffer would block the child forever.
    """

    def pump() -> None:
        if stream is None:  # pragma: no cover - both pipes are always created
            return

        try:
            for line in stream:
                sink.append(line)
                if callback is not None:
                    # A listener that raises must not stop the pipe from being
                    # drained: a full pipe buffer would block the child forever,
                    # which is a hang the user cannot cancel out of.
                    with contextlib.suppress(Exception):
                        callback(line.rstrip("\r\n"))
        except (OSError, ValueError):  # pragma: no cover - stream closed under us
            pass

    thread = threading.Thread(target=pump, name="pypackhub-runner-reader", daemon=True)
    thread.start()

    return thread


@dataclass(slots=True)
class _OutputHandlers:
    stdout: Callable[[str], None] | None = None
    stderr: Callable[[str], None] | None = None


def run(
    argv: Sequence[str],
    *,
    timeout: float | None = DEFAULT_TIMEOUT,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    cancel: threading.Event | None = None,
    stdin_text: str | None = None,
    on_stdout: Callable[[str], None] | None = None,
    on_stderr: Callable[[str], None] | None = None,
    check: bool = False,
) -> CommandResult:
    """Run a command and return its result.

    The result is always returned - a timeout or a cancel is reported through
    :attr:`CommandResult.timed_out` / :attr:`CommandResult.cancelled` with
    whatever output was produced, so the UI can explain what happened instead
    of losing the log. Pass ``check=True`` to also raise
    :class:`CommandFailedError` for a non-zero exit status.
    """

    arguments = validate_argv(argv)
    program = arguments[0]

    # An absolute path is used as given; a bare name is looked up through a
    # hardened search path. Either way the child never inherits a relative path
    # or a PATH entry that points into the current working directory.
    executable = resolve_executable(program)

    handlers = _OutputHandlers(stdout=on_stdout, stderr=on_stderr)
    environment = sanitized_environment(env)
    creationflags = 0
    start_new_session = False

    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0
        )
    else:
        start_new_session = True

    command = (executable, *arguments[1:])
    started = time.monotonic()

    process = subprocess.Popen(  # noqa: S603 - argument list only, resolved executable
        command,
        cwd=str(cwd) if cwd is not None else None,
        env=environment,
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        close_fds=True,
        creationflags=creationflags,
        start_new_session=start_new_session,
    )

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    readers = [
        _start_reader(process.stdout, stdout_lines, handlers.stdout),
        _start_reader(process.stderr, stderr_lines, handlers.stderr),
    ]

    if stdin_text is not None and process.stdin is not None:
        try:
            process.stdin.write(stdin_text)
        except OSError:  # pragma: no cover - child closed stdin early
            pass
        finally:
            with contextlib.suppress(OSError):  # pragma: no cover - already closed
                process.stdin.close()

    deadline = None if timeout is None else started + timeout
    timed_out = False
    cancelled = False

    try:
        while True:
            if process.poll() is not None:
                break
            if cancel is not None and cancel.is_set():
                cancelled = True
                terminate_process_tree(process)
                break
            if deadline is not None and time.monotonic() > deadline:
                timed_out = True
                terminate_process_tree(process)
                break
            time.sleep(_POLL_INTERVAL)
    finally:
        try:
            process.wait(timeout=GRACE_PERIOD)
        except subprocess.TimeoutExpired:  # pragma: no cover - kill() already attempted
            process.kill()
            process.wait()
        for reader in readers:
            reader.join(timeout=GRACE_PERIOD)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    result = CommandResult(
        argv=command,
        returncode=process.returncode if process.returncode is not None else -1,
        stdout="".join(stdout_lines),
        stderr="".join(stderr_lines),
        duration=time.monotonic() - started,
        timed_out=timed_out,
        cancelled=cancelled,
    )

    if check and not result.ok:
        raise CommandFailedError(result)

    return result


# ---------------------------------------------------------------------------
# Elevated execution (SPEC section 11.5)
# ---------------------------------------------------------------------------

#: Windows error code the UAC consent dialog leaves behind when the user
#: answered "No". Reported as a *cancellation*: the command never ran, so
#: nothing failed - the user stopped it before it could.
_ERROR_CANCELLED: Final = 1223

#: ``WAIT_TIMEOUT`` from the Windows API: the handle is still there, keep waiting.
_WAIT_TIMEOUT: Final = 258

#: ``WAIT_FAILED`` from the Windows API: the wait itself is broken.
_WAIT_FAILED: Final = 0xFFFFFFFF

#: How long the elevated-process handle is waited on between two looks at
#: the output file the elevated helper writes - the streaming heartbeat.
_ELEVATED_POLL_MS: Final = 250


class _OutputFileTail:
    """Follow the growth of one file, in complete lines.

    The elevated helper writes the run's output to a file this process
    created; this reads whatever is new since the last look and hands back
    whole lines only - a progress line that arrives in halves waits for its
    terminator rather than being shown as two broken ones. ``splitlines``
    treats a lone carriage return as a terminator, which is what turns
    pip's in-place download updates into one line each, the same way the
    pipe readers of :func:`run` see them.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._offset = 0
        self._pending = ""

    def drain(self) -> list[str]:
        """The complete lines that arrived since the last call."""
        try:
            with self._path.open("rb") as handle:
                handle.seek(self._offset)
                blob = handle.read()
        except OSError:  # pragma: no cover - the file exists before the run
            return []
        self._offset += len(blob)
        return self._feed(blob.decode("utf-8", errors="replace"))

    def flush(self) -> list[str]:
        """The unterminated remainder, as the run's final lines."""
        remainder = self._pending
        self._pending = ""
        return [line for line in remainder.splitlines() if line]

    def _feed(self, text: str) -> list[str]:
        if not text:
            return []
        text = self._pending + text
        self._pending = ""
        lines = text.splitlines()
        if not text.endswith(("\r", "\n")):
            self._pending = lines[-1] if lines else ""
            lines = lines[:-1] if lines else []
        return [line for line in lines if line]


def _elevated_helper_path() -> Path:
    """Absolute path of the helper module an elevated run starts.

    Empty when the module's file cannot be located - a frozen build, say -
    in which case the caller falls back to elevating the command directly.
    """
    from pypackhub.core import elevated_helper

    module_file = getattr(elevated_helper, "__file__", "") or ""
    return Path(module_file).resolve() if module_file else Path()


#: How long the elevated-process handle gets to disappear after a terminate,
#: before the exit code is read anyway.
_ELEVATED_GRACE_MS: Final = int(GRACE_PERIOD * 1000)


def elevation_supported() -> bool:
    """Whether the OS offers a standard elevation mechanism this runner can use.

    Windows has UAC - ``ShellExecuteEx`` with the ``runas`` verb - and Linux
    has polkit's ``pkexec``, which takes an executable plus its argument list
    like this runner passes every command. macOS is the deliberate exception:
    its only route is embedding the command in an AppleScript string, which is
    exactly the string-command execution the security rules forbid, so the
    answer is False there and the UI offers pip instead.
    """
    if os.name == "nt":
        return True
    if sys.platform == "darwin":
        return False
    return shutil.which("pkexec") is not None


def run_elevated(
    argv: Sequence[str],
    *,
    timeout: float | None = DEFAULT_TIMEOUT,
    cancel: threading.Event | None = None,
    on_line: Callable[[str], None] | None = None,
) -> CommandResult:
    """Run one confirmed command through the OS's standard elevation mechanism.

    SPEC section 11.5 in code: the elevation covers a *single* action the UI
    has already explained, it goes through the OS's own consent mechanism
    (never a re-run of this process as root), and nothing about it persists
    after the command ends.

    What is executed obeys the same rules as :func:`run`: an argument list
    only, an explicitly resolved executable, a timeout enforced against the
    real process. Output crosses the consent boundary the one way it can:
    on Windows the elevated half is a stateless helper of ours that
    redirects the approved command's output to a file this process created
    and follows while the command runs (see
    :mod:`pypackhub.core.elevated_helper`); on polkit the standard runner
    reads the pipes ``pkexec`` itself provides. Either way ``on_line``
    receives the run's real lines as they arrive - the percentages that
    move a progress bar, the error lines that explain a failure - and the
    result carries them for the log and the history.

    A user who declines the OS's prompt is reported as a cancellation for the
    same reason a stopped process is: the command never ran, and "cancelled"
    is what the UI already says in the user's language.
    """
    arguments = validate_argv(argv)
    executable = resolve_executable(arguments[0])
    command = (executable, *arguments[1:])

    if os.name == "nt":
        return _run_elevated_windows(command, timeout=timeout, cancel=cancel, on_line=on_line)

    return _run_elevated_posix(command, timeout=timeout, cancel=cancel, on_line=on_line)


def _run_elevated_windows(
    command: tuple[str, ...],
    *,
    timeout: float | None,
    cancel: threading.Event | None,
    on_line: Callable[[str], None] | None = None,
) -> CommandResult:
    """Ask UAC to run the command, then supervise the elevated process.

    ``ShellExecuteEx`` with the ``runas`` verb is the OS's own mechanism: the
    consent dialog is Windows', the elevated token is granted by Windows, and
    this process stays unprivileged throughout. The parameters string is
    built with :func:`subprocess.list2cmdline` - the canonical quoting for a
    Windows argument vector - from arguments that already passed the core
    validators; no shell is involved at any point.

    When the caller wants the run's output, the elevated half is
    :mod:`pypackhub.core.elevated_helper`: the helper starts the approved
    command with its output redirected to a file this process created,
    this process follows the file while the command runs, and the helper
    exits with the command's exit code. Without an ``on_line`` - or where
    the helper file cannot be located - the command is elevated directly,
    exactly as this runner always ran it.
    """
    import ctypes
    import importlib

    # Reached through ``getattr``/``importlib`` on purpose: they exist only on
    # Windows, and the type checker is configured to check this tree as POSIX,
    # so a direct ``ctypes.WinDLL`` would be rejected here while being the one
    # thing that must run there (the same convention the platform-specific
    # constants elsewhere in this module follow).
    wintypes = importlib.import_module("ctypes.wintypes")
    windows_dll = getattr(ctypes, "WinDLL")  # noqa: B009 - absent on the POSIX type-check platform
    last_error = getattr(ctypes, "get_last_error")  # noqa: B009 - absent on the POSIX platform

    see_mask_nocloseprocess = 0x00000040
    sw_hide = 0

    class ShellExecuteInfoW(ctypes.Structure):
        """``SHELLEXECUTEINFOW``; the union member is a plain ``HANDLE``."""

        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    shell32 = windows_dll("shell32", use_last_error=True)
    kernel32 = windows_dll("kernel32", use_last_error=True)

    info = ShellExecuteInfoW()
    info.cbSize = ctypes.sizeof(ShellExecuteInfoW)
    info.fMask = see_mask_nocloseprocess
    info.lpVerb = "runas"

    # The streaming route: the unprivileged process owns the file - it
    # creates it, the elevated helper writes to it, and only these two
    # ever see it. Every line the approved command prints arrives there
    # and is drained into ``on_line`` while the elevated process runs.
    tail: _OutputFileTail | None = None
    lines: list[str] = []
    log_path: str | None = None
    helper = _elevated_helper_path() if on_line is not None else Path()

    if on_line is not None and sys.executable and helper.is_file():
        descriptor, log_path = tempfile.mkstemp(prefix="pypackhub-elev-", suffix=".log")
        os.close(descriptor)
        tail = _OutputFileTail(Path(log_path))
        info.lpFile = sys.executable
        info.lpParameters = subprocess.list2cmdline([str(helper), log_path, *command])
    else:
        info.lpFile = command[0]
        info.lpParameters = subprocess.list2cmdline(command[1:])
    info.nShow = sw_hide

    def report(new_lines: list[str]) -> None:
        lines.extend(new_lines)
        if on_line is None:
            return
        for line in new_lines:
            # A listener that raises must not stop the supervision, for the
            # same reason the pipe readers of :func:`run` keep draining.
            with contextlib.suppress(Exception):
                on_line(line)

    started = time.monotonic()

    try:
        if not shell32.ShellExecuteExW(ctypes.byref(info)):
            code = last_error()
            if code == _ERROR_CANCELLED:
                return CommandResult(
                    argv=command,
                    returncode=-1,
                    stdout="",
                    stderr="",
                    duration=time.monotonic() - started,
                    cancelled=True,
                )
            return CommandResult(
                argv=command,
                returncode=-1,
                stdout="",
                stderr=f"the elevated command could not be started (Windows error {code})",
                duration=time.monotonic() - started,
            )

        process = info.hProcess
        if not process:  # pragma: no cover - NOCLOSEPROCESS promises a handle
            return CommandResult(
                argv=command,
                returncode=-1,
                stdout="",
                stderr="the elevation succeeded but no process handle was returned",
                duration=time.monotonic() - started,
            )

        deadline = None if timeout is None else started + timeout
        timed_out = False
        cancelled = False

        while True:
            # The file the elevated helper writes is this process's window
            # onto the run: each pass takes what arrived since the last.
            if tail is not None:
                report(tail.drain())
            state = kernel32.WaitForSingleObject(process, _ELEVATED_POLL_MS)
            if state != _WAIT_TIMEOUT:
                break
            if cancel is not None and cancel.is_set():
                cancelled = True
                kernel32.TerminateProcess(process, 1)
                kernel32.WaitForSingleObject(process, _ELEVATED_GRACE_MS)
                break
            if deadline is not None and time.monotonic() > deadline:
                timed_out = True
                kernel32.TerminateProcess(process, 1)
                kernel32.WaitForSingleObject(process, _ELEVATED_GRACE_MS)
                break

        if tail is not None:
            # Whatever the ending was, the file may hold lines the loop never
            # saw - including the ones a killed child managed to write.
            report(tail.drain())
            report(tail.flush())

        exit_code = wintypes.DWORD(_WAIT_FAILED)
        kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code))
        kernel32.CloseHandle(process)

        returncode = -1 if exit_code.value == _WAIT_FAILED else int(exit_code.value)
        return CommandResult(
            argv=command,
            returncode=returncode,
            stdout="".join(f"{line}\n" for line in lines),
            stderr="",
            duration=time.monotonic() - started,
            timed_out=timed_out,
            cancelled=cancelled,
        )
    finally:
        if log_path is not None:
            # The log file is this process's own scratch: the run is over
            # and every line it held is in the result.
            with contextlib.suppress(OSError):
                Path(log_path).unlink()


def _run_elevated_posix(
    command: tuple[str, ...],
    *,
    timeout: float | None,
    cancel: threading.Event | None,
    on_line: Callable[[str], None] | None = None,
) -> CommandResult:
    """Run the command through polkit, or explain that there is no way to.

    ``pkexec`` is polkit's standard mechanism and takes an executable plus its
    argument list - no shell is involved anywhere - so the hardened runner
    applies unchanged, and polkit carries pipes: the run streams its real
    output exactly like any other command's, cancellation works the same
    way, and the result carries the same log.
    """
    if sys.platform == "darwin":
        return CommandResult(
            argv=command,
            returncode=-1,
            stdout="",
            stderr=(
                "macOS offers no elevation mechanism that accepts an argument "
                "list; use pip for this environment instead"
            ),
            duration=0.0,
        )

    if shutil.which("pkexec") is None:
        return CommandResult(
            argv=command,
            returncode=-1,
            stdout="",
            stderr="pkexec (polkit) was not found, so this command cannot be elevated here",
            duration=0.0,
        )

    return run(
        ("pkexec", *command),
        timeout=timeout,
        cancel=cancel,
        on_stdout=on_line,
        on_stderr=on_line,
    )
