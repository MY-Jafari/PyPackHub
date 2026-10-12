"""Installed-package sizes for PyPackHub.

How much disk space does one installed package use? The honest answer is already
recorded on disk: the installer writes a ``RECORD`` file into every
``*.dist-info`` directory, listing every file it installed together with its
size. Reading that file is one small read per distribution, and it is exact - no
walk over the environment, no guess from a directory name.

That distinction matters, and it is the reason this module was rewritten. pip and
uv report a package's *location* as the environment it lives in, so a
location-based walk measured the whole environment once per package: every row in
the table showed the same, far too large number, and measuring a 200-package
environment walked the same tree 200 times. Sizes now come from the distribution's
own record, and a distribution whose record cannot be read is reported as *not
measured* instead of as zero bytes.
"""

from __future__ import annotations

import contextlib
import csv
from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

__all__ = [
    "PackageSize",
    "distribution_size",
    "package_sizes",
]

#: A ``RECORD`` file with more entries than this is not a distribution record any
#: more. The cap keeps a corrupt or hostile file from filling memory.
MAX_RECORD_ENTRIES: Final = 200_000

#: How many top-level entries :attr:`PackageSize.top_paths` keeps.
TOP_PATH_LIMIT: Final = 5

#: The file an installer writes next to the metadata, listing what it installed.
RECORD_NAME: Final = "RECORD"

#: The suffix of an installed distribution directory.
DIST_INFO_SUFFIX: Final = ".dist-info"


@dataclass(slots=True)
class PackageSize:
    """How much disk space one installed distribution appears to use."""

    name: str
    #: The ``*.dist-info`` directory the numbers were read from.
    location: Path
    files_count: int
    size_bytes: int
    #: The largest top-level entries (the import package, its data, the metadata
    #: directory) with their size, biggest first. Handy for "what is taking the
    #: space" without reading the record again.
    top_paths: tuple[tuple[str, int], ...] = ()
    #: True when the numbers came from the distribution's own ``RECORD``. False
    #: means they are a best-effort measurement of the distribution's directory.
    exact: bool = True

    @property
    def size_human(self) -> str:
        value = float(self.size_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                break
            value /= 1024
        return f"{value:.1f} {unit}"


def _record_rows(record: Path) -> Iterator[tuple[str, int | None]]:
    """Yield ``(relative path, declared size)`` for one ``RECORD`` file.

    ``RECORD`` is CSV: a path, an optional hash and an optional size, written by
    the installer. A malformed tail stops the iteration rather than raising - a
    half-written record still describes most of the distribution, and the table
    must not fail because a package manager crashed mid-install.
    """
    try:
        with record.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            for index, row in enumerate(csv.reader(handle)):
                if index >= MAX_RECORD_ENTRIES:
                    break
                if not row:
                    continue
                entry = row[0].strip()
                if not entry:
                    continue
                size: int | None = None
                if len(row) >= 3 and row[2].strip().isdigit():
                    size = int(row[2])
                yield entry, size
    except (OSError, ValueError, csv.Error):
        return


def _record_parts(relative: str) -> list[str] | None:
    """The path parts of one ``RECORD`` entry, or ``None`` when it escapes.

    A record is untrusted input: it is a file inside a wheel that a stranger
    published. ``../../etc/whatever``, an absolute path and a Windows drive
    reference are all refused here, before anything is opened or measured.
    """
    text = relative.strip().replace("\\", "/")
    if not text or text.startswith("/") or ":" in text:
        return None

    parts = [part for part in text.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        return None

    return parts


def _file_size(root: Path, parts: Sequence[str]) -> int | None:
    """The size of one recorded file, when the record did not declare one.

    Only called for the entries an installer left a size out of: pip writes one,
    uv does not, and the difference on this machine is twenty thousand file
    lookups in a single environment. The ``Path`` is built here rather than for
    every entry for the same reason.
    """
    try:
        return root.joinpath(*parts).stat().st_size
    except OSError:
        return None


def _rank(top_paths: dict[str, int]) -> tuple[tuple[str, int], ...]:
    """The biggest top-level entries, largest first."""
    ranked = sorted(top_paths.items(), key=lambda item: item[1], reverse=True)
    return tuple(ranked[:TOP_PATH_LIMIT])


def _measure_record(dist_info: Path) -> tuple[int, int, tuple[tuple[str, int], ...]] | None:
    """Measure a distribution from its ``RECORD``, or ``None`` when there is none."""
    record = dist_info / RECORD_NAME
    if not record.is_file():
        return None

    site_packages = dist_info.parent
    files = 0
    total = 0
    by_top: dict[str, int] = {}

    for relative, declared in _record_rows(record):
        parts = _record_parts(relative)
        if parts is None:
            continue

        size = declared
        if size is None:
            size = _file_size(site_packages, parts)
        if size is None:
            continue

        files += 1
        total += size
        top = parts[0]
        by_top[top] = by_top.get(top, 0) + size

    return files, total, _rank(by_top)


def _walk(path: Path, by_top: dict[str, int]) -> tuple[int, int]:
    """Sum the regular files under *path*, bucketed by *path*'s own name."""
    files = 0
    total = 0

    if path.is_file():
        with contextlib.suppress(OSError):
            size = path.stat().st_size
            files = 1
            total = size
            by_top[path.name] = by_top.get(path.name, 0) + size
        return files, total

    if not path.is_dir():
        return 0, 0

    try:
        for candidate in path.rglob("*"):
            if not candidate.is_file():
                continue
            with contextlib.suppress(OSError):
                size = candidate.stat().st_size
                files += 1
                total += size
                by_top[path.name] = by_top.get(path.name, 0) + size
    except (OSError, ValueError):
        pass

    return files, total


def _measure_without_record(dist_info: Path) -> tuple[int, int, tuple[tuple[str, int], ...]] | None:
    """Best effort for a distribution with no readable ``RECORD``.

    Only the distribution's own directory and a sibling named after it are
    walked - never the environment, which is what made the old measurement both
    slow and wrong. This is an estimate, and :attr:`PackageSize.exact` says so.
    """
    site_packages = dist_info.parent
    stem = (
        dist_info.name[: -len(DIST_INFO_SUFFIX)]
        if dist_info.name.endswith(DIST_INFO_SUFFIX)
        else dist_info.name
    )
    package_name = stem.split("-", 1)[0]
    by_top: dict[str, int] = {}
    files = 0
    total = 0

    candidates = [dist_info]
    for sibling in (site_packages / package_name, site_packages / f"{package_name}.py"):
        if sibling != dist_info and sibling.exists():
            candidates.append(sibling)

    for candidate in candidates:
        counted_files, counted_size = _walk(candidate, by_top)
        files += counted_files
        total += counted_size

    if not files:
        return None

    return files, total, _rank(by_top)


def _name_from_dist_info(dist_info: Path) -> str:
    """The distribution name a ``*.dist-info`` directory belongs to."""
    stem = (
        dist_info.name[: -len(DIST_INFO_SUFFIX)]
        if dist_info.name.endswith(DIST_INFO_SUFFIX)
        else dist_info.name
    )
    return stem.split("-", 1)[0]


def distribution_size(dist_info: Path, *, name: str = "") -> PackageSize | None:
    """Measure one installed distribution from its ``*.dist-info`` directory.

    *dist_info* is the distribution directory - the one pip and uv list in their
    ``RECORD``-carrying metadata layout - not the environment. ``None`` means the
    distribution has no readable record and nothing measurable of its own, which
    the caller shows as "not measured" rather than as zero bytes.
    """
    directory = Path(dist_info)
    if not directory.is_dir():
        return None

    measured = _measure_record(directory)
    exact = measured is not None
    if measured is None:
        measured = _measure_without_record(directory)
    if measured is None:
        return None

    files, total, top_paths = measured
    return PackageSize(
        name=name or _name_from_dist_info(directory),
        location=directory,
        files_count=files,
        size_bytes=total,
        top_paths=top_paths,
        exact=exact,
    )


def package_sizes(distributions: Collection[tuple[str, Path]]) -> dict[str, PackageSize]:
    """Return the on-disk size for each distribution, keyed by package name.

    Each pair is a package name and its ``*.dist-info`` directory. A
    distribution that cannot be measured is absent from the result, so the table
    leaves its size unknown instead of claiming it is empty.
    """
    result: dict[str, PackageSize] = {}

    for name, dist_info in distributions:
        measured = distribution_size(Path(dist_info), name=name)
        if measured is not None:
            result[name] = measured

    return result
