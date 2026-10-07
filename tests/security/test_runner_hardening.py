"""SPEC section 11.1: the process runner's guarantees, tested against real children.

The tests below spawn real processes with the interpreter running the suite,
because the things being checked - the environment a child sees, whether a
timeout actually kills it - cannot be verified against a mock.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

from pypackhub.core.runner import (
    CommandFailedError,
    CommandResult,
    ExecutableNotFoundError,
    InvalidCommandError,
    resolve_executable,
    run,
    safe_search_path,
    sanitized_environment,
)

pytestmark = pytest.mark.security

PYTHON = sys.executable


def test_a_relative_executable_is_refused() -> None:
    for name in ("./pip", "../scripts/pip", "bin/pip", "nested/tool.exe"):
        with pytest.raises(InvalidCommandError):
            resolve_executable(name)


def test_an_option_like_executable_is_refused() -> None:
    with pytest.raises(InvalidCommandError):
        resolve_executable("-rf")


def test_an_unknown_tool_is_reported_as_not_found() -> None:
    with pytest.raises(ExecutableNotFoundError):
        resolve_executable("pypackhub-tool-that-does-not-exist")


def test_a_bare_name_resolves_through_the_search_path(tmp_path: Path) -> None:
    # Built here rather than borrowed from the machine, so the test means the
    # same thing on every platform and in every CI image.
    tool = tmp_path / ("pypackhub-tool.exe" if os.name == "nt" else "pypackhub-tool")
    tool.write_text("", encoding="utf-8")
    if os.name != "nt":
        tool.chmod(0o755)

    resolved = resolve_executable("pypackhub-tool", search_path=str(tmp_path))

    assert Path(resolved).is_absolute()
    # Windows reports the match with the extension from PATHEXT, so the
    # comparison has to be case-insensitive there.
    assert Path(resolved).name.lower() == tool.name.lower()


def test_an_absolute_path_is_accepted_and_must_exist() -> None:
    assert resolve_executable(PYTHON) == PYTHON

    with pytest.raises(ExecutableNotFoundError):
        resolve_executable(str(Path(PYTHON).with_name("not-python-here")))


def test_empty_arguments_are_refused() -> None:
    with pytest.raises(InvalidCommandError):
        run([])
    with pytest.raises(InvalidCommandError):
        run([""])


def test_nul_bytes_are_refused() -> None:
    with pytest.raises(InvalidCommandError):
        run([PYTHON, "-c", "print('bad')\x00"])


def test_sanitized_environment_drops_anything_that_can_redirect_an_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/tmp/sneaky")
    monkeypatch.setenv("VIRTUAL_ENV", "/tmp/other-env")
    monkeypatch.setenv("PIP_INDEX_URL", "https://evil.example/simple")
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://evil.example/simple")
    monkeypatch.setenv("UV_INDEX_URL", "https://evil.example/simple")
    monkeypatch.setenv("PYTHONHOME", "/tmp/nowhere")

    environment = sanitized_environment()

    for variable in (
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "UV_INDEX_URL",
        "PYTHONHOME",
    ):
        assert variable not in environment, variable


def test_sanitized_environment_keeps_what_tools_need() -> None:
    environment = sanitized_environment()

    assert environment["PATH"]
    assert environment["PYTHONUNBUFFERED"] == "1"
    assert environment["PYTHONIOENCODING"] == "utf-8"
    # pip must never wait for a prompt that a GUI cannot answer.
    assert environment["PIP_NO_INPUT"] == "1"


def test_relative_and_empty_path_entries_are_dropped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    separator = os.pathsep
    absolute = str(tmp_path)
    monkeypatch.setenv("PATH", f".{separator}{separator}relative-dir{separator}{absolute}")

    entries = safe_search_path().split(separator)

    assert "." not in entries
    assert "" not in entries
    assert "relative-dir" not in entries
    assert absolute in entries


def test_output_environment_variables_are_passed_through() -> None:
    environment = sanitized_environment({"PYPACKHUB_TEST_VALUE": "yes"})

    assert environment["PYPACKHUB_TEST_VALUE"] == "yes"


def test_environment_values_with_nul_bytes_are_refused() -> None:
    with pytest.raises(InvalidCommandError):
        sanitized_environment({"PYPACKHUB_BAD": "a\x00b"})


def test_a_child_cannot_see_pythonpath(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "/tmp/sneaky")

    result = run(
        [PYTHON, "-c", "import os; print(os.environ.get('PYTHONPATH', 'absent'))"],
        timeout=60,
    )

    assert result.ok
    assert result.stdout.strip() == "absent"


def test_run_returns_output_and_exit_status() -> None:
    result = run([PYTHON, "-c", "print('hello from the child')"], timeout=60)

    assert result.ok
    assert result.returncode == 0
    assert "hello from the child" in result.stdout
    assert result.duration > 0


def test_stderr_and_stdout_are_kept_apart() -> None:
    result = run(
        [PYTHON, "-c", "import sys; print('out'); print('err', file=sys.stderr)"],
        timeout=60,
    )

    assert "out" in result.stdout
    assert "out" not in result.stderr
    assert "err" in result.stderr
    assert "err" in result.output


def test_output_is_streamed_to_the_callback() -> None:
    seen: list[str] = []

    result = run(
        [PYTHON, "-u", "-c", "print('first'); print('second')"],
        timeout=60,
        on_stdout=seen.append,
    )

    assert result.ok
    assert seen == ["first", "second"]


def test_a_broken_listener_does_not_break_the_drain() -> None:
    def explode(_: str) -> None:
        raise RuntimeError("listener is broken")

    result = run([PYTHON, "-c", "print('still collected')"], timeout=60, on_stdout=explode)

    assert result.ok
    assert "still collected" in result.stdout


def test_a_failing_command_is_reported_not_raised() -> None:
    result = run([PYTHON, "-c", "import sys; sys.exit(3)"], timeout=60)

    assert result.ok is False
    assert result.returncode == 3


def test_check_mode_raises_with_the_result() -> None:
    with pytest.raises(CommandFailedError) as info:
        run([PYTHON, "-c", "import sys; sys.exit(4)"], timeout=60, check=True)

    assert info.value.result.returncode == 4


def test_a_timeout_kills_the_child() -> None:
    result = run([PYTHON, "-c", "import time; time.sleep(120)"], timeout=1.0)

    assert result.timed_out is True
    assert result.ok is False
    assert result.duration < 60


def test_a_cancel_kills_the_child() -> None:
    cancel = threading.Event()
    timer = threading.Timer(0.5, cancel.set)
    timer.start()

    try:
        result = run(
            [PYTHON, "-c", "import time; time.sleep(120)"],
            timeout=120,
            cancel=cancel,
        )
    finally:
        timer.cancel()

    assert result.cancelled is True
    assert result.ok is False
    assert result.duration < 60


def test_a_cancelled_child_that_ignores_signals_is_still_killed() -> None:
    cancel = threading.Event()
    script = (
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "time.sleep(120)\n"
    )
    timer = threading.Timer(0.5, cancel.set)
    timer.start()

    try:
        result = run([PYTHON, "-c", script], timeout=0.5, cancel=cancel)
    finally:
        timer.cancel()

    assert result.ok is False
    assert result.duration < 60


def test_stdin_text_reaches_the_child() -> None:
    result = run(
        [PYTHON, "-c", "import sys; print(sys.stdin.read().strip().upper())"],
        timeout=60,
        stdin_text="hello\n",
    )

    assert result.ok
    assert "HELLO" in result.stdout


def test_display_command_is_quoted_but_still_the_same_arguments() -> None:
    result = CommandResult(
        argv=("pip", "install", "requests >= 2.31"),
        returncode=0,
        stdout="",
        stderr="",
        duration=0.0,
    )

    shown = result.display_command()

    assert shown.startswith("pip install ")
    assert '"requests >= 2.31"' in shown


def test_display_command_redacts_credentials() -> None:
    result = CommandResult(
        argv=("pip", "install", "--index-url=https://user:secret@mirror.example/simple"),
        returncode=0,
        stdout="",
        stderr="",
        duration=0.0,
    )

    shown = result.display_command()

    assert "secret" not in shown
    assert "mirror.example" in shown
