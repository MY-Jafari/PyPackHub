"""Installed distribution metadata: read locally, never over the network.

The package detail panel needs things PyPI cannot answer honestly: which
requirements the *installed* distribution declares, who depends on it here, and
where it actually lives. All of that is on disk next to the packages themselves,
in each ``*.dist-info/METADATA`` file.

The reader here is deliberately small and strict:

* the metadata format is RFC 822, so :mod:`email.parser` (stdlib, no code
  execution, no ``eval``) is the parser rather than a hand-rolled one;
* every file read is bounded, and a malformed or missing file yields an empty
  record rather than an exception - a broken third-party package must not break
  the window;
* package names are compared with PEP 503 normalisation, because ``Foo_Bar`` and
  ``foo-bar`` are the same distribution.

``scan`` reads every distribution once and returns an index, which is what makes
"required-by" answerable: it is built from the union of every ``Requires-Dist``
in the environment, not from a second network call.

A base interpreter has **two** package directories, not one: its own, and the user
site directory that ``pip install --user`` writes to. On an all-users Windows
install the second one holds almost everything, because the interpreter refuses to
write into the first, so a reader that only looks inside the prefix can show
hundreds of packages with no metadata, no size and no dependency edges at all.
"""

from __future__ import annotations

import contextlib
import email
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from email.message import Message
from pathlib import Path
from typing import Final

from packaging.requirements import InvalidRequirement, Requirement

from pypackhub.core.platform_guard import EnvironmentKind, inspect

__all__ = [
    "InstalledMetadataIndex",
    "PackageMetadata",
    "directory_writable",
    "distribution_directories",
    "normalize_name",
    "own_site_packages_directories",
    "read_metadata",
    "scan",
    "site_packages_writable",
]

#: A ``METADATA`` file larger than this is not metadata any more. The cap keeps a
#: corrupt or hostile file from being loaded into memory.
MAX_METADATA_BYTES: Final = 512 * 1024

#: The suffix pip uses for installed distribution directories.
DIST_INFO_SUFFIX: Final = ".dist-info"

#: The file an installer writes into a ``.dist-info`` directory when the package
#: on the command line was the one the user asked for, rather than one pulled in
#: to satisfy a requirement. Read for orphan detection, nothing more: its absence
#: is meaningful, its presence is not a promise.
REQUESTED_MARKER: Final = "REQUESTED"

#: ``site-packages`` locations inside a Python prefix, most specific first.
_SITE_PACKAGE_GLOBS: Final = (
    "Lib/site-packages",
    "lib/site-packages",
    "lib/python*/site-packages",
    "lib64/python*/site-packages",
    "lib/pypy*/site-packages",
    "local/lib/python*/site-packages",
)


def normalize_name(name: str) -> str:
    """Normalise a distribution name the way PEP 503 does."""
    return name.strip().lower().replace("_", "-").replace(".", "-")


@dataclass(frozen=True, slots=True)
class PackageMetadata:
    """What one installed distribution declares about itself."""

    name: str
    version: str = ""
    summary: str = ""
    license: str = ""
    author: str = ""
    home_page: str = ""
    project_url: str = ""
    requires_python: str = ""
    location: Path | None = None
    #: ``Requires-Dist`` entries, validated: markers and extras are kept in the
    #: rendered text but a malformed entry is dropped rather than shown raw.
    requires: tuple[str, ...] = ()
    #: ``Provides-Extra`` entries.
    extras: tuple[str, ...] = ()
    #: Names of the installed distributions that require this one.
    required_by: tuple[str, ...] = field(default=())
    #: True when the user asked for this package by name.
    #:
    #: The installer writes a ``REQUESTED`` marker into the ``.dist-info``
    #: directory of every distribution named on a command line, and only those.
    #: It is the installer's own record of "the user wanted this one", which is
    #: what makes an orphan a fact rather than a guess: a package with no
    #: dependents *and* no marker was pulled in to satisfy something else.
    requested: bool = False

    @property
    def normalized_name(self) -> str:
        return normalize_name(self.name)


def _parse_posix_version(name: str) -> tuple[int, int] | None:
    """``python3.13`` -> ``(3, 13)``."""
    text = name.removeprefix("python")
    parts = text.split(".", 1)
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    return int(parts[0]), int(parts[1])


def _parse_windows_version(name: str) -> tuple[int, int] | None:
    """``python313.dll`` -> ``(3, 13)``."""
    text = name.removeprefix("python").removesuffix(".dll")
    if len(text) < 2 or not text.isdigit():
        return None
    return int(text[0]), int(text[1:])


def _interpreter_version(prefix: Path) -> tuple[int, int] | None:
    """The ``(major, minor)`` of a base interpreter, read from its own layout.

    Nothing is executed to find this out: a CPython prefix names its version on
    disk - ``lib/python3.13`` on POSIX, ``python313.dll`` on Windows - and that is
    all the user site path needs to be worked out.
    """
    patterns = (
        ("lib/python3.*", _parse_posix_version),
        ("lib64/python3.*", _parse_posix_version),
        ("python3*.dll", _parse_windows_version),
    )

    for pattern, parse in patterns:
        for candidate in sorted(prefix.glob(pattern)):
            version = parse(candidate.name)
            if version is not None:
                return version

    return None


def _user_site_directories(prefix: Path) -> tuple[Path, ...]:
    """The user site directories that belong to a *base* interpreter.

    ``pip install --user`` writes outside the prefix, and a virtual environment is
    left alone on purpose: pip inside one ignores the user site, so including it
    here would attribute another environment's packages to this one.
    """
    if (prefix / "pyvenv.cfg").is_file():
        return ()

    version = _interpreter_version(prefix)
    if version is None:
        return ()

    major, minor = version
    candidates: list[Path] = []

    override = os.environ.get("PYTHONUSERBASE")
    if override:
        root = Path(override).expanduser()
        candidates.extend(
            (
                root / f"Python{major}{minor}" / "site-packages",
                root / "lib" / f"python{major}.{minor}" / "site-packages",
            )
        )

    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "Python" / f"Python{major}{minor}" / "site-packages")

    home = Path.home()
    candidates.append(home / ".local" / "lib" / f"python{major}.{minor}" / "site-packages")
    if sys.platform == "darwin":
        candidates.append(
            home / "Library" / "Python" / f"{major}.{minor}" / "lib" / "python" / "site-packages"
        )

    return tuple(candidate for candidate in candidates if candidate.is_dir())


def _dedupe_directories(paths: list[Path]) -> tuple[Path, ...]:
    """Drop directories that repeat through symlinks or ``lib``/``lib64`` twins.

    A virtual environment on POSIX keeps its packages directly under ``lib/``,
    and the ``lib64`` of several distributions is a symlink back to it, so the
    same tree is otherwise read once per name it can be reached by.
    """
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return tuple(unique)


def own_site_packages_directories(prefix: Path) -> tuple[Path, ...]:
    """The prefix's own existing ``site-packages`` directories.

    These are the directories a package manager writes into on behalf of this
    environment. The user site directory is deliberately absent: it belongs
    to the *user*, not to the interpreter, and no code that means "where does
    this environment keep its packages" may be answered with it.
    """
    found: list[Path] = []

    for pattern in _SITE_PACKAGE_GLOBS:
        if "*" in pattern:
            found.extend(sorted(path for path in prefix.glob(pattern) if path.is_dir()))
        else:
            candidate = prefix / pattern
            if candidate.is_dir():
                found.append(candidate)

    return _dedupe_directories(found)


def distribution_directories(prefix: Path) -> tuple[Path, ...]:
    """Every existing ``site-packages`` directory that belongs to a prefix.

    That is the prefix's own directories plus, for a real base interpreter, the
    user site directory its ``--user`` installs land in. A directory tree with no
    interpreter in it is not a base interpreter, so nothing is added for it: a
    test fixture, or an environment whose interpreter was removed, keeps answering
    for itself alone.
    """
    found: list[Path] = list(own_site_packages_directories(prefix))

    facts = inspect(prefix)
    if facts.kind is EnvironmentKind.SYSTEM and facts.executable is not None:
        found.extend(_user_site_directories(prefix))

    return _dedupe_directories(found)


def site_packages_writable(prefix: Path) -> bool:
    """Whether the current user can write into *prefix*'s own site-packages.

    This is the fact behind "uv needs administrator privileges": a backend with
    no user-site path of its own can only write the environment's own
    site-packages, and on an all-users install that directory is exactly the
    one a normal user may not change - ``Program Files`` on Windows,
    ``/usr/lib`` elsewhere. pip needs no help there because it falls back to
    the user site; that fallback is why the *user site* directories are not
    part of this answer.

    ``os.access`` is not the test: on Windows it reads the read-only file
    attribute rather than the ACLs, and happily reports "writable" for a
    ``Program Files`` directory the current user cannot write into at all.
    Creating and removing a probe file is the honest test on every platform.

    An environment with no own site-packages directory is not refused here:
    the package manager will produce its own, more precise error, and
    inventing an elevation request to answer a guess would be worse.
    """
    directories = own_site_packages_directories(prefix)
    if not directories:
        return True

    return any(_directory_is_writable(directory) for directory in directories)


def directory_writable(directory: Path) -> bool:
    """Whether a file can be created inside *directory* right now.

    The probe behind :func:`site_packages_writable`, on its own for the
    callers whose writable location is not a site-packages directory: a
    project folder, which is what a project tool's commands write before
    anything else. ``os.access`` is not the test, for the reasons
    :func:`site_packages_writable` documents.
    """
    return _directory_is_writable(directory)


def _directory_is_writable(directory: Path) -> bool:
    """Whether a file can be created inside *directory* right now."""
    probe = directory / ".pypackhub-write-probe"
    try:
        probe.write_text("", encoding="utf-8")
    except OSError:
        return False

    with contextlib.suppress(OSError):
        probe.unlink()
    return True


def _dist_info_directories(prefix: Path) -> Iterator[Path]:
    for directory in distribution_directories(prefix):
        yield from sorted(directory.glob(f"*{DIST_INFO_SUFFIX}"))


def _message(path: Path) -> Message | None:
    """Parse one metadata file, or return ``None`` when it cannot be read."""
    try:
        if path.stat().st_size > MAX_METADATA_BYTES:
            return None
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return email.message_from_string(handle.read())
    except (OSError, ValueError):
        return None


def _field(message: Message, *names: str) -> str:
    for name in names:
        value = message.get(name)
        if value:
            return str(value).strip()
    return ""


def _requirements(message: Message) -> tuple[str, ...]:
    """Validated ``Requires-Dist`` entries.

    A requirement that ``packaging`` refuses is dropped: showing an unparsed
    requirement string in the UI would imply we understood it, and it is the
    kind of value that a later feature might act on.
    """
    values: list[str] = []
    for raw in message.get_all("Requires-Dist", []):
        text = str(raw).strip()
        try:
            requirement = Requirement(text)
        except InvalidRequirement:
            continue
        name = requirement.name
        if requirement.extras:
            name = f"{name}[{','.join(sorted(requirement.extras))}]"
        if requirement.specifier:
            name = f"{name}{requirement.specifier}"
        if requirement.marker:
            name = f"{name}; {requirement.marker}"
        values.append(name)

    return tuple(values)


def read_metadata(location: Path) -> PackageMetadata:
    """Read one distribution's metadata from its directory.

    *location* is the ``.dist-info`` directory. A missing or unreadable
    ``METADATA`` file returns an empty record named after the directory, which
    keeps "package exists but declares nothing" distinguishable from "not found".
    """
    directory_name = location.name
    stem = (
        directory_name[: -len(DIST_INFO_SUFFIX)]
        if directory_name.endswith(DIST_INFO_SUFFIX)
        else directory_name
    )
    name, _, version = stem.partition("-")

    requested = (location / REQUESTED_MARKER).is_file()

    message = _message(location / "METADATA")
    if message is None:
        return PackageMetadata(name=name, version=version, location=location, requested=requested)

    return PackageMetadata(
        name=_field(message, "Name") or name,
        version=_field(message, "Version") or version,
        summary=_field(message, "Summary"),
        license=_field(message, "License", "License-Expression"),
        author=_field(message, "Author", "Author-email", "Maintainer"),
        home_page=_field(message, "Home-page"),
        project_url=_field(message, "Project-URL"),
        requires_python=_field(message, "Requires-Python"),
        location=location,
        requires=_requirements(message),
        extras=tuple(str(value).strip() for value in message.get_all("Provides-Extra", [])),
        requested=requested,
    )


@dataclass(frozen=True, slots=True)
class InstalledMetadataIndex:
    """Every installed distribution in one environment, plus reverse deps."""

    environment: Path
    packages: dict[str, PackageMetadata] = field(default_factory=dict)
    #: normalised distribution name -> names that require it.
    required_by: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def get(self, name: str) -> PackageMetadata | None:
        return self.packages.get(normalize_name(name))

    def dependents_of(self, name: str) -> tuple[str, ...]:
        return self.required_by.get(normalize_name(name), ())

    def __len__(self) -> int:
        return len(self.packages)


def scan(prefix: Path) -> InstalledMetadataIndex:
    """Read every installed distribution in *prefix* and index the reverse deps.

    This is the expensive part of the detail panel, which is why it is a separate
    call: the caller runs it on a worker thread, once per environment (or per
    refresh), and every selected package is then answered from memory.
    """
    packages: dict[str, PackageMetadata] = {}

    for directory in _dist_info_directories(prefix):
        record = read_metadata(directory)
        key = record.normalized_name
        if not key:
            continue
        packages[key] = record

    dependents: dict[str, set[str]] = {}
    for record in packages.values():
        for requirement in record.requires:
            try:
                required_name = Requirement(requirement).name
            except InvalidRequirement:  # pragma: no cover - already validated
                continue
            dependents.setdefault(normalize_name(required_name), set()).add(
                normalize_name(record.name)
            )

    return InstalledMetadataIndex(
        environment=prefix,
        packages=packages,
        # Built from what was found rather than from the installed names, so a
        # package that is required but missing from this environment is still
        # reported as having dependents - that is exactly the state the user
        # needs to see.
        required_by={name: tuple(sorted(names)) for name, names in dependents.items()},
    )
