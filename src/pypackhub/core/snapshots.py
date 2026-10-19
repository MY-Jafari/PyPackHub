"""Environment snapshots: what was installed, recorded before it changes.

A snapshot is a small JSON file - one row per installed distribution, name and
version, nothing else - written under the application's data directory. It is
the safety net behind two features:

* **Rollback.** The diff between a snapshot and the current listing is a
  preview of what restoring it would do: packages to reinstall at their
  recorded versions, and packages that arrived since and would leave.
* **Undo.** Every changing action the packages view runs takes an automatic
  snapshot first, so the History page can offer to put those exact versions
  back - even for an uninstall, where "install it again" is meaningless
  without the version that was there.

The store is deliberately boring: plain files in a per-environment directory,
named after the moment they were taken, parsed with ``json`` and validated on
the way in. A snapshot file is *our own* data, but files are files: a corrupt
or truncated entry is reported as unreadable rather than crashing the page
that listed it.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pypackhub.core.settings import settings_data_directory

__all__ = [
    "Snapshot",
    "SnapshotDiff",
    "SnapshotStore",
    "restore_specs",
]


#: A snapshot id is a UTC timestamp rendered so it sorts by name and is a safe
#: filename on every platform. The last group is milliseconds: an undo batch
#: can capture more than one snapshot inside a single second, and those
#: captures must not overwrite each other.
_ID_PATTERN: Final = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{3}$")

#: Refuse to read a snapshot file larger than this: a snapshot is a list of
#: name/version pairs, and anything bigger is not one.
MAX_SNAPSHOT_BYTES: Final = 4 * 1024 * 1024

#: How many automatic snapshots are kept per environment. The manual ones are
#: never trimmed; the automatic ones exist to be useful this week, not forever.
AUTOMATIC_KEEP: Final = 25


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One recorded state of one environment's installed packages."""

    id: str
    environment: str
    created_at: float
    automatic: bool
    #: Why the snapshot exists - a free-form note, usually the exact command it
    #: was taken before, so the history can show it next to the action.
    reason: str
    #: ``(name, version)`` for every installed distribution, name-sorted.
    packages: tuple[tuple[str, str], ...]

    @property
    def version_of(self) -> dict[str, str]:
        """The rows as a lookup, by normalised name (PEP 503)."""
        return {normalize(name): version for name, version in self.packages}

    @property
    def package_count(self) -> int:
        return len(self.packages)


def normalize(name: str) -> str:
    """Normalise a distribution name the way PEP 503 does."""
    return name.strip().lower().replace("_", "-").replace(".", "-")


def _next_id(directory: Path) -> str:
    """A fresh, chronological id that no existing snapshot in *directory* uses."""
    moment = time.time()
    stamp = time.strftime("%Y-%m-%dT%H-%M-%S", time.gmtime(moment))
    milliseconds = int((moment % 1) * 1000)

    for attempt in range(1000):
        # The suffix must stay three digits: it is part of the id pattern
        # every reader validates against, so a wider number would write a
        # file nothing can ever read back.
        suffix = milliseconds + attempt
        if suffix > 999:
            break
        candidate = f"{stamp}-{suffix:03d}"
        if not (directory / f"{candidate}.json").exists():
            return candidate

    # A thousand snapshots inside one millisecond is not a state a desktop
    # application reaches; refusing loudly beats overwriting quietly.
    raise OSError("could not find a free snapshot id for this moment")


@dataclass(frozen=True, slots=True)
class SnapshotDiff:
    """What restoring a snapshot would do, computed before anything runs.

    Every entry carries the version the snapshot recorded, so a caller can
    build exact pins from the diff alone, without re-reading the file.
    """

    #: ``(name, snapshot_version)`` for packages in the snapshot but missing
    #: now: they will be reinstalled at the recorded version.
    removed: tuple[tuple[str, str], ...]
    #: Names installed now but absent from the snapshot: a full restore would
    #: uninstall them.
    added: tuple[str, ...]
    #: ``(name, snapshot_version, current_version)`` for every package whose
    #: version moved since the snapshot.
    changed: tuple[tuple[str, str, str], ...]

    @property
    def is_empty(self) -> bool:
        return not (self.removed or self.added or self.changed)


class SnapshotStore:
    """Snapshots on disk, one directory per environment."""

    def __init__(self, *, root: Path | None = None) -> None:
        self._root = Path(root) if root is not None else settings_data_directory() / "snapshots"

    # -- writing --------------------------------------------------------

    def capture(
        self,
        environment: Path,
        packages: list[tuple[str, str]],
        *,
        automatic: bool = False,
        reason: str = "",
    ) -> Snapshot:
        """Record the installed packages of *environment* and store the file.

        The id is the capture moment, so ids sort chronologically by name.
        Millisecond collisions (two captures inside the same millisecond)
        bump the id rather than overwrite: a snapshot must never silently
        replace another.
        """
        snapshot_id = _next_id(self._directory_for(str(Path(environment).resolve())))
        rows = sorted((name, version) for name, version in packages)

        snapshot = Snapshot(
            id=snapshot_id,
            environment=str(Path(environment).resolve()),
            created_at=time.time(),
            automatic=automatic,
            reason=reason,
            packages=tuple(rows),
        )

        directory = self._directory_for(snapshot.environment)
        directory.mkdir(parents=True, exist_ok=True)
        self._write(directory / f"{snapshot_id}.json", snapshot)
        if automatic:
            self._trim_automatic(snapshot.environment)

        return snapshot

    def _write(self, path: Path, snapshot: Snapshot) -> None:
        payload = {
            "format": 1,
            "id": snapshot.id,
            "environment": snapshot.environment,
            "created_at": snapshot.created_at,
            "automatic": snapshot.automatic,
            "reason": snapshot.reason,
            "packages": [list(row) for row in snapshot.packages],
        }
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(path)

    def _trim_automatic(self, environment: str) -> None:
        """Keep only the newest automatic snapshots for *environment*."""
        automatic = [entry for entry in self.list_snapshots(environment) if entry.automatic]
        for stale in automatic[AUTOMATIC_KEEP:]:
            self.delete(environment, stale.id)

    # -- reading --------------------------------------------------------

    def list_snapshots(self, environment: str) -> list[Snapshot]:
        """The snapshots for one environment, newest first.

        An unreadable file is skipped rather than raising: one corrupt
        snapshot must not make the rest of the history invisible.
        """
        directory = self._directory_for(environment)
        if not directory.is_dir():
            return []

        snapshots: list[Snapshot] = []
        for path in sorted(directory.glob("*.json"), reverse=True):
            snapshot = self._read(path)
            if snapshot is not None:
                snapshots.append(snapshot)
        return snapshots

    def load(self, environment: str, snapshot_id: str) -> Snapshot | None:
        """One snapshot by id, or ``None`` when it is gone or unreadable."""
        if not _ID_PATTERN.match(snapshot_id):
            return None
        return self._read(self._directory_for(environment) / f"{snapshot_id}.json")

    def _read(self, path: Path) -> Snapshot | None:
        try:
            if path.stat().st_size > MAX_SNAPSHOT_BYTES:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

        if not isinstance(payload, dict):
            return None

        snapshot_id = str(payload.get("id") or "")
        if not _ID_PATTERN.match(snapshot_id):
            return None

        rows: list[tuple[str, str]] = []
        raw_packages = payload.get("packages")
        if isinstance(raw_packages, list):
            for row in raw_packages:
                if not isinstance(row, list) or len(row) != 2:
                    continue
                name, version = str(row[0]), str(row[1])
                if name and version:
                    rows.append((name, version))

        return Snapshot(
            id=snapshot_id,
            environment=str(payload.get("environment") or ""),
            created_at=float(payload.get("created_at") or 0.0),
            automatic=bool(payload.get("automatic")),
            reason=str(payload.get("reason") or ""),
            packages=tuple(rows),
        )

    # -- diffing -------------------------------------------------------

    def diff(
        self,
        snapshot: Snapshot,
        installed: list[tuple[str, str]],
    ) -> SnapshotDiff:
        """What a restore of *snapshot* would change, given what is installed now.

        Names are compared PEP 503-normalised, because the listing and the
        snapshot may disagree about underscores and case without meaning
        anything by it.
        """
        recorded = snapshot.version_of
        current = {normalize(name): version for name, version in installed}

        removed: list[tuple[str, str]] = []
        added: list[str] = []
        changed: list[tuple[str, str, str]] = []

        for name, version in sorted(recorded.items()):
            if name not in current:
                removed.append((name, version))
            elif current[name] != version:
                changed.append((name, version, current[name]))

        for name in sorted(current):
            if name not in recorded:
                added.append(name)

        return SnapshotDiff(
            removed=tuple(removed),
            added=tuple(added),
            changed=tuple(changed),
        )

    # -- deleting ------------------------------------------------------

    def delete(self, environment: str, snapshot_id: str) -> bool:
        """Remove one snapshot. True when something was removed."""
        if not _ID_PATTERN.match(snapshot_id):
            return False

        path = self._directory_for(environment) / f"{snapshot_id}.json"
        try:
            path.unlink()
            return True
        except OSError:
            return False

    # -- layout --------------------------------------------------------

    def _directory_for(self, environment: str) -> Path:
        """The per-environment directory, addressed by a hash of the prefix.

        The prefix is a path, and paths belong in file contents, not in
        directory names: a hash keeps every platform-legal character out of
        the layout and no traversal is possible. The hash is always taken of
        the *resolved* prefix, so a caller may pass the path in any spelling.
        """
        key = str(Path(environment).resolve()) if environment else ""
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        return self._root / digest


def restore_specs(diff: SnapshotDiff) -> tuple[str, ...]:
    """The requirement specs that bring a snapshot's rows back, exactly.

    ``name==version`` for every package the snapshot remembers that is now
    missing or different - pinned, because "any version" is not a restore.
    Added packages are deliberately absent: they are the user's newer
    decisions, and removing them is a separate, explicit choice (the preview
    shows them, the caller decides).
    """
    specs = [f"{name}=={version}" for name, version in diff.removed]
    specs.extend(f"{name}=={version}" for name, version, _current in diff.changed)
    return tuple(sorted(specs))
