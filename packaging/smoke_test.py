"""Prove that a freshly built bundle actually runs and is complete.

A successful PyInstaller run only means the files were written. The two ways a
desktop bundle fails are both silent at that point and embarrassing later:

* a *missing* data file - a font, an icon, certifi's CA bundle - that only shows
  up on a machine where nothing else provides it;
* an *over-aggressive exclusion* - the spec drops QtWebEngine and friends, and a
  single wrong entry turns "starts in a quarter of a second" into "dies before
  the window exists".

So this script does both halves. It first checks that the bundle contains the
files the application needs at runtime, then launches the real executable with
Qt's offscreen platform and insists that it is still alive after a grace period.
It deliberately does not import the application: only the built artifact is
under test, which is the whole point of running it here.

Usage::

    python packaging/smoke_test.py                # dist/ next to the repository
    python packaging/smoke_test.py --dist DIR
    python packaging/smoke_test.py --grace 20

Exit code 0 means the bundle is complete and started without crashing.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]

#: Relative to the bundle, for each platform we might be checking.
EXECUTABLE_CANDIDATES: Final = (
    "PyPackHub.app/Contents/MacOS/PyPackHub",
    "PyPackHub/PyPackHub.exe",
    "PyPackHub/PyPackHub",
)

#: Every one of these has to exist somewhere inside the built bundle. Each entry
#: maps a glob to what it is for, because the frozen layout differs between
#: one-directory builds, a macOS ``.app`` and the ``_internal`` subdirectory
#: PyInstaller 6 uses.
REQUIRED_FILES: Final = {
    "**/assets/fonts/inter/Inter-Regular.ttf": "the Inter face used by the Latin UI",
    "**/assets/fonts/vazirmatn/Vazirmatn-Regular.ttf": (
        "the Vazirmatn face used by the Persian UI"
    ),
    "**/assets/fonts/jetbrains-mono/JetBrainsMono-Regular.ttf": (
        "the mono face used by the log panel"
    ),
    "**/assets/icons/app.svg": "the application icon",
    "**/certifi/cacert.pem": ("certifi's CA bundle, without which HTTPS cannot be verified"),
}

#: The Qt platform plugins the application needs. The offscreen plugin is
#: required everywhere because that is what the launch check below uses; the
#: native one is what a user actually gets.
REQUIRED_PLUGINS: Final = {
    "win32": {
        "**/platforms/qoffscreen.dll": "the offscreen Qt platform plugin",
        "**/platforms/qwindows.dll": "the Windows Qt platform plugin",
    },
    "darwin": {
        "**/platforms/libqoffscreen.dylib": "the offscreen Qt platform plugin",
        "**/platforms/libqcocoa.dylib": "the macOS Qt platform plugin",
    },
}.get(
    sys.platform,
    {
        "**/platforms/libqoffscreen.so": "the offscreen Qt platform plugin",
        "**/platforms/libqxcb.so": "the X11 Qt platform plugin",
    },
)

#: Substrings that mean the process is reporting a problem rather than starting.
#: Only meaningful where stdout and stderr are captured at all: a windowed
#: Windows build has no console, so there the survival check is the signal.
FAILURE_MARKERS: Final = (
    "traceback (most recent call last)",
    "modulenotfounderror",
    "no module named",
    "this application failed to start",
    "could not load the qt platform plugin",
    "could not find the qt platform plugin",
)


def fail(message: str) -> SystemExit:
    """A failed check, reported as one line instead of a Python traceback."""

    return SystemExit(f"smoke test failed: {message}")


def missing_from(dist: Path, wanted: dict[str, str]) -> list[str]:
    """Descriptions of the expected files that are not in the bundle."""

    return [description for pattern, description in wanted.items() if not any(dist.glob(pattern))]


def locate_executable(dist: Path) -> Path:
    """Find the built executable inside ``dist``, whatever the platform laid out."""

    for candidate in EXECUTABLE_CANDIDATES:
        path = dist / candidate
        if path.is_file():
            return path

    found = sorted(path.name for path in dist.rglob("PyPackHub*") if path.is_file())
    hint = f" (found {', '.join(found)})" if found else ""
    raise fail(f"no PyPackHub executable under {dist}{hint}")


def isolated_environment(root: Path) -> dict[str, str]:
    """An environment that keeps the launched app away from the real user's data.

    platformdirs decides where settings, logs and snapshots live from these
    variables, so pointing them at a scratch directory means the smoke test
    cannot read, write or delete anything belonging to the account running CI -
    and the run starts from a genuinely empty profile, which is what a fresh
    install looks like.
    """

    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)

    environment = dict(os.environ)
    environment.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "HOME": str(home),
            "USERPROFILE": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_STATE_HOME": str(home / ".local" / "state"),
            "APPDATA": str(home / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(home / "AppData" / "Local"),
        }
    )

    # A frozen bundle must not inherit a PYTHONPATH or PYTHONHOME pointing at the
    # build environment: those would mask exactly the packaging mistakes this
    # test exists to find.
    for variable in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        environment.pop(variable, None)

    return environment


def launch(executable: Path, environment: dict[str, str], grace: float) -> str:
    """Start the bundle, insist it survives ``grace`` seconds, then stop it."""

    creation_flags = 0
    if sys.platform == "win32":
        # No console window should flash up on a developer's machine.
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    process = subprocess.Popen(
        [str(executable)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        cwd=str(executable.parent),
        text=True,
        errors="replace",
        creationflags=creation_flags,
    )

    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and process.poll() is None:
        time.sleep(0.25)

    if process.poll() is not None:
        output = process.stdout.read() if process.stdout is not None else ""
        raise fail(
            f"the application exited on its own with code {process.returncode} "
            f"after less than {grace:g}s\n--- output ---\n"
            f"{output.strip() or '(none captured)'}"
        )

    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=15)

    output = process.stdout.read() if process.stdout is not None else ""
    if process.stdout is not None:
        process.stdout.close()

    return output


def scan_output(output: str) -> None:
    """Reject output that shows a problem the survival check cannot see."""

    lowered = output.lower()
    for marker in FAILURE_MARKERS:
        if marker in lowered:
            raise fail("the application reported a problem while starting:\n" + output.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="smoke_test",
        description="Check that a built PyPackHub bundle is complete and starts.",
    )
    parser.add_argument(
        "--dist",
        type=Path,
        default=REPOSITORY_ROOT / "dist",
        help="the directory PyInstaller wrote to (default: ./dist)",
    )
    parser.add_argument(
        "--grace",
        type=float,
        default=12.0,
        help="seconds the application must stay alive (default: 12)",
    )
    arguments = parser.parse_args(argv)

    dist = arguments.dist.resolve()
    if not dist.is_dir():
        raise fail(f"{dist} does not exist; build the bundle first")

    executable = locate_executable(dist)
    print(f"executable: {executable.relative_to(dist)}")

    incomplete = missing_from(dist, REQUIRED_FILES) + missing_from(dist, REQUIRED_PLUGINS)
    if incomplete:
        raise fail("the bundle is incomplete, missing " + "; ".join(incomplete))

    files = [path for path in dist.rglob("*") if path.is_file()]
    megabytes = sum(path.stat().st_size for path in files) // (1024 * 1024)
    print(f"contents: {len(files)} files, {megabytes} MiB")
    print(f"data files and Qt plugins: all {len(REQUIRED_FILES) + len(REQUIRED_PLUGINS)} present")

    with tempfile.TemporaryDirectory(prefix="pypackhub-smoke-") as scratch:
        environment = isolated_environment(Path(scratch))
        print("launching with QT_QPA_PLATFORM=offscreen from an empty profile")

        output = launch(executable, environment, arguments.grace)
        scan_output(output)

        written = sorted(
            str(path.relative_to(scratch)) for path in Path(scratch).rglob("*") if path.is_file()
        )

    if written:
        # The honest record of what a first run creates, without which nobody
        # notices an application that writes into its own installation.
        print("a fresh profile contains: " + ", ".join(written[:12]))

    print(f"ok: the bundle stayed alive for {arguments.grace:g}s with no error output")
    return 0


if __name__ == "__main__":  # pragma: no cover - run by the release workflow
    raise SystemExit(main())
