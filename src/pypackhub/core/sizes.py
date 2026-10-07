"""Installed-package sizes for PyPackHub.

A package's dist metadata lists the files it installed, but not the size of
those files. Computing the size of one package therefore costs a handful of
stat calls, which is why this module computes sizes lazily in a worker-friendly
way and caches them by the package's installed location rather than by name.

Nothing here is a GUI concept. It returns plain numbers and the paths that were
counted so a UI layer can render them however it wants.
"""

from __future__ import annotations

import contextlib
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "PackageSize",
    "package_sizes",
]


@dataclass(slots=True)
class PackageSize:
    """How much disk space one installed package appears to use."""

    name: str
    location: Path
    files_count: int
    size_bytes: int
    top_paths: tuple[tuple[str, int], ...] = ()

    @property
    def size_human(self) -> str:
        value = self.size_bytes
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                break
            value /= 1024
        return f"{value:.1f} {unit}"


def _iter_files_under(prefix: Path) -> Collection[Path]:
    """Enumerate the files under *prefix*, stopping at the top level of folders.

    For site-packages we intentionally keep the counting shallow: a package may
    own a folder with thousands of files, and walking everything every time
    would make size lookups expensive. The section 2 skeleton keeps this simple;
    a real implementation can swap this for a cached manifest or a deeper walk
    when it matters.
    """
    try:
        return sorted(prefix.rglob("*"), key=lambda path: path)
    except (OSError, ValueError):
        return []


def _top_paths(paths: Collection[Path], top: int = 5) -> tuple[tuple[str, int], ...]:
    by_dir: dict[str, int] = {}
    for path in paths:
        if not path.is_file():
            continue
        try:
            parent = path.parent.relative_to(path.parent.parent)
        except ValueError:
            parent = path.parent.name
        by_dir[str(parent)] = by_dir.get(str(parent), 0) + 1

    ranked = sorted(by_dir.items(), key=lambda item: item[1], reverse=True)
    return tuple(ranked[:top])


def package_sizes(
    packages: Collection[tuple[str, Path]],
    *,
    max_files_without_walking: int = 4096,
) -> dict[str, PackageSize]:
    """Return the on-disk size estimate for each installed package.

    ``packages`` is expected to be the output of a backend's listing step: a
    package name plus the install location it claims. Paths that do not exist on
    disk are reported as zero rather than raising, because a listing can be
    stale and the UI should not crash on it.
    """
    result: dict[str, PackageSize] = {}

    for name, location in packages:
        resolved = location
        if not resolved.is_dir():
            result[name] = PackageSize(
                name=name,
                location=resolved,
                files_count=0,
                size_bytes=0,
            )
            continue

        files: list[Path] = []
        size = 0
        try:
            for candidate in resolved.rglob("*"):
                if not candidate.is_file():
                    continue
                files.append(candidate)
                with contextlib.suppress(OSError):
                    size += candidate.stat().st_size
                if len(files) >= max_files_without_walking:
                    break
        except (OSError, ValueError):
            pass

        result[name] = PackageSize(
            name=name,
            location=resolved,
            files_count=len(files),
            size_bytes=size,
            top_paths=_top_paths(files),
        )

    return result
