"""Disk-usage analysis: where the bytes of an environment actually are.

Three answers, all for the environment the window is looking at, all
read-only:

* **How big is it?** The environment's total size, by walking the prefix -
  which is the honest number, because it includes the interpreter, the
  ``pyvenv.cfg`` and every stray file the packages dropped outside their
  ``RECORD``.
* **What is biggest?** The per-package measurements from
  :mod:`pypackhub.core.sizes`, ranked, so the page can show the largest
  consumers instead of making a person sort a table by hand.
* **What about the caches?** pip's, uv's and poetry's download caches and
  conda's package cache grow without anybody deciding anything; each one is
  located the way its tool does (honouring the environment variables that
  move it) and measured, so the number can be looked at before it is a
  problem.

Nothing here deletes anything. Cache cleaning is a change to the machine and
belongs behind a confirmation like every other change; inspection is free.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from pypackhub.core.metadata import scan
from pypackhub.core.sizes import PackageSize, package_sizes

__all__ = [
    "CacheUsage",
    "EnvironmentUsage",
    "analyse_disk_usage",
    "conda_pkgs_dir",
    "directory_size",
    "pip_cache_dir",
    "poetry_cache_dir",
    "uv_cache_dir",
]

#: How many of the largest consumers an analysis carries. The view can show
#: fewer; the number exists so a huge environment cannot make the panel a
#: table of ten thousand rows.
_LARGEST: int = 12


@dataclass(frozen=True, slots=True)
class CacheUsage:
    """One package-manager cache directory and what it holds."""

    #: "pip" or "uv" - which tool's cache this is.
    kind: str
    path: Path
    size_bytes: int
    #: False when the directory does not exist: a cache never created is a
    #: fact ("0 bytes, nothing there"), not an error.
    exists: bool


@dataclass(frozen=True, slots=True)
class EnvironmentUsage:
    """The disk-usage analysis of one environment."""

    environment: Path
    #: The whole prefix, walked: interpreter, metadata, everything.
    total_bytes: int
    #: How many files the walk saw. Informational, but it is what makes
    #: "412 MB" mean something when the next refresh says 413.
    files: int
    #: False when parts of the tree could not be read: the total is then a
    #: floor, not a ceiling, and the panel says so.
    complete: bool
    #: Per-package measurements, largest first.
    packages: tuple[PackageSize, ...]
    #: The package-manager caches this machine keeps, measured.
    caches: tuple[CacheUsage, ...]


def directory_size(path: Path) -> tuple[int, int, bool]:
    """The byte size, file count and completeness of a directory tree.

    Walks with a stack rather than recursion: a deeply nested cache must not
    be able to exhaust the stack of the worker that is measuring it.
    Unreadable entries are skipped and remembered - a locked file lowers the
    total by its size but not by a crash.
    """
    total = 0
    files = 0
    complete = True

    stack: list[Path] = [path]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            complete = False
            continue

        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
                    files += 1
            except OSError:
                complete = False

    return total, files, complete


def _xdg_cache_home() -> Path:
    override = os.environ.get("XDG_CACHE_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache"


def pip_cache_dir() -> Path:
    """Where pip keeps its download cache, the way pip itself decides.

    ``PIP_CACHE_DIR`` wins, then each platform's convention. No subprocess is
    spawned to ask pip: the resolution is documented and stable, and a
    desktop application measuring a cache should not pay a process start to
    learn a path it can compute.
    """
    override = os.environ.get("PIP_CACHE_DIR")
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "pip" / "Cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "pip"
    return _xdg_cache_home() / "pip"


def uv_cache_dir() -> Path:
    """Where uv keeps its cache, honouring ``UV_CACHE_DIR`` the way uv does."""
    override = os.environ.get("UV_CACHE_DIR")
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "uv" / "cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "uv"
    return _xdg_cache_home() / "uv"


def conda_pkgs_dir() -> Path | None:
    """Where conda keeps downloaded packages, or ``None`` when it is not decided.

    ``CONDA_PKGS_DIRS`` (a path-list, first entry wins) and ``CONDA_ROOT`` are
    conda's own documented knobs, and they are the only ones honoured: a
    package cache that lives somewhere neither variable names is invisible
    here rather than guessed, because a guessed cache path would report a
    number for a directory that may not be conda's at all.
    """
    listed = os.environ.get("CONDA_PKGS_DIRS", "")
    first = listed.split(os.pathsep)[0].strip() if listed else ""
    if first:
        return Path(first).expanduser()

    root = os.environ.get("CONDA_ROOT", "").strip()
    if root:
        return Path(root).expanduser() / "pkgs"
    return None


def poetry_cache_dir() -> Path:
    """Where poetry keeps its cache, the way poetry itself decides.

    No subprocess is spawned to ask poetry: the platform defaults are
    documented and stable, and the application only uses the path to show
    what the cache holds.
    """
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "pypoetry" / "Cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "pypoetry"
    return _xdg_cache_home() / "pypoetry"


def _cache_usage(kind: str, path: Path) -> CacheUsage:
    if not path.exists():
        return CacheUsage(kind=kind, path=path, size_bytes=0, exists=False)
    size, _files, _complete = directory_size(path)
    return CacheUsage(kind=kind, path=path, size_bytes=size, exists=True)


def analyse_disk_usage(prefix: Path) -> EnvironmentUsage:
    """Measure one environment: its total, its largest packages, the caches.

    This is the pass the section-9 gate exercises over a synthetic
    environment: pure filesystem arithmetic, no subprocess, no network, safe
    to run from any thread.
    """
    environment = Path(prefix).resolve()
    total, files, complete = directory_size(environment)

    index = scan(environment)
    targets: list[tuple[str, Path]] = []
    for metadata in index.packages.values():
        if metadata.location:
            targets.append((str(metadata.name), Path(metadata.location)))
    measured = package_sizes(targets)
    largest = tuple(sorted(measured.values(), key=lambda size: size.size_bytes, reverse=True))

    entries: list[tuple[str, Path]] = [
        ("pip", pip_cache_dir()),
        ("uv", uv_cache_dir()),
        ("poetry", poetry_cache_dir()),
    ]
    conda_dir = conda_pkgs_dir()
    if conda_dir is not None:
        # Conda's cache is only reported when conda's own variables say
        # where it is: an installation that configured nothing reports
        # nothing rather than a guess.
        entries.append(("conda", conda_dir))
    caches = tuple(_cache_usage(kind, cache_dir) for kind, cache_dir in entries)

    return EnvironmentUsage(
        environment=environment,
        total_bytes=total,
        files=files,
        complete=complete,
        packages=largest[:_LARGEST],
        caches=caches,
    )
