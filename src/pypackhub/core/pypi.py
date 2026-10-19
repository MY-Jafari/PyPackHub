"""PyPI helper for PyPackHub.

This module is the small, policy-enforcing layer over :mod:`pypackhub.core.net`
that answers the questions the package view needs: does this package exist, what
versions are available, what metadata does PyPI carry, and is the cached answer
still fresh enough to show locally.

The point of the cache is not to avoid the network entirely - PyPI has no batch
endpoint, and a desktop app must still refresh version information - but to make
the fast path local. The section 2 skeleton keeps the cache in memory and tiny;
the table view will fill it lazily, and later sections add a disk-backed cache.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Any, Final

from pypackhub.core.net import HttpClient, HttpError, ResponseTooLargeError, is_offline_error
from pypackhub.core.validation import validate_package_name

__all__ = [
    "PackageInfo",
    "PackageMeta",
    "PackageNotFoundError",
    "PyPICache",
    "PyPIClient",
    "ReleaseFacts",
    "pypi_info",
]


PYPI_JSON_BASE: Final = "https://pypi.org/pypi"
PYPI_SIMPLE_BASE: Final = "https://pypi.org/simple"

#: How many cached lookups one client keeps before the least recently used
#: start being evicted. Large enough that a full refresh's worth of packages
#: stays warm; small enough that a long session cannot grow without end.
_MAX_CACHE_ENTRIES: Final = 1024


def _json_base_for(index_url: str | None) -> str:
    """The JSON endpoint root that belongs to *index_url*.

    An index is configured as its simple-index root (``.../simple``); the
    JSON API this client reads lives next to it (``.../pypi``). Anything
    that already looks like the JSON root is taken as-is, and no URL means
    the public index.
    """
    if not index_url:
        return PYPI_JSON_BASE

    trimmed = index_url.rstrip("/")
    if trimmed.endswith("/pypi"):
        return trimmed
    if trimmed.endswith("/simple"):
        return trimmed[: -len("/simple")] + "/pypi"
    return trimmed + "/pypi"


@dataclass(slots=True)
class PackageInfo:
    """Everything the application needs from PyPI about one package."""

    name: str
    canonical_name: str
    latest_version: str
    summary: str = ""
    home_page: str = ""
    project_url: str = ""
    requires_python: str = ""
    classifiers: tuple[str, ...] = ()
    last_checked: float = 0.0
    fetched: bool = False
    error: str = ""


@dataclass(slots=True)
class PackageMeta:
    """A richer metadata blob for the detail panel."""

    info: PackageInfo
    versions: tuple[str, ...]
    requires_dist: tuple[str, ...] = ()
    requires_external: tuple[str, ...] = ()
    provides_extra: tuple[str, ...] = ()
    size_bytes: int = 0
    filename: str = ""


class PackageNotFoundError(ValueError):
    """The package exists locally but PyPI had no matching entry."""


@dataclass(frozen=True, slots=True)
class ReleaseFacts:
    """What the index knows about one package's releases (SPEC 11.3).

    Fetched once when a trust panel is asked for, and evaluated into flags by
    :func:`pypackhub.core.trust.evaluate_trust`; the dates are ISO
    ``YYYY-MM-DD`` strings because they are shown to a person, not computed
    on. ``error`` set means the index could not be asked, which is a state
    the panel must render differently from "everything is fine".
    """

    name: str
    first_release: str = ""
    releases: int = 0
    latest_release: str = ""
    #: Whether the newest release ships a wheel. An sdist-only package
    #: executes code the moment it is installed; a wheel does not.
    latest_has_wheel: bool = False
    homepage: str = ""
    license: str = ""
    error: str = ""

    @property
    def known(self) -> bool:
        """False when nothing could be fetched - distinct from "fine"."""
        return not self.error


class PyPICache:
    """A bounded, thread-safe in-memory cache for PyPI lookups.

    Entries expire by age and the store keeps at most *max_entries*, evicting
    the least recently used. The bound exists because the table asks about
    every package it has ever listed: without one, a long session over a
    large environment grows memory without end. Lookups run from several
    worker threads, so every access takes the lock.
    """

    def __init__(self, *, ttl: float = 300.0, max_entries: int = 1024) -> None:
        self._ttl = ttl
        self._max_entries = max(8, int(max_entries))
        self._store: OrderedDict[str, PackageInfo] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, name: str) -> PackageInfo | None:
        with self._lock:
            info = self._store.get(name)
            if info is None:
                return None
            if monotonic() - info.last_checked > self._ttl:
                del self._store[name]
                return None
            self._store.move_to_end(name)
            return info

    def set(self, info: PackageInfo) -> None:
        with self._lock:
            self._store[info.name] = info
            self._store.move_to_end(info.name)
            while len(self._store) > self._max_entries:
                self._store.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


class PyPIClient:
    """PyPI client with an HTTPS-only HTTP layer and a small TTL cache.

    ``index_url`` is the package index the user configured - a simple-index
    root such as ``https://pypi.org/simple`` or a private mirror. The JSON
    endpoints this client reads are derived from it, so lookups answer from
    the index the user actually use, not silently from the public one. When
    it is empty the public index is used.
    """

    def __init__(
        self,
        *,
        http: HttpClient | None = None,
        cache: PyPICache | None = None,
        ttl: float | None = None,
        index_url: str | None = None,
        max_versions: int = 32,
    ) -> None:
        self._http = http or HttpClient(allow_insecure_http=False)
        self._cache = cache or PyPICache(
            ttl=ttl if ttl is not None else 300.0,
            max_entries=_MAX_CACHE_ENTRIES,
        )
        self._json_base = _json_base_for(index_url)
        self._max_versions = max(1, int(max_versions))

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> PyPIClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def info(self, package: str) -> PackageInfo:
        """Return PyPI info for *package*, using cache first, then the network."""
        canonical = validate_package_name(package)
        cached = self._cache.get(canonical)

        if cached is not None:
            return cached

        return self._fetch_info(canonical)

    def meta(self, package: str) -> PackageMeta:
        """Return metadata plus available versions for *package*."""
        info = self.info(package)

        if info.error:
            return PackageMeta(info=info, versions=())

        versions = self._fetch_versions(info.name)
        pydist = self._fetch_distributions(info.name, info.latest_version)

        return PackageMeta(
            info=info,
            versions=versions,
            requires_dist=tuple(pydist.get("requires_dist") or ()),
            requires_external=tuple(pydist.get("requires_external") or ()),
            provides_extra=tuple(pydist.get("provides_extra") or ()),
            size_bytes=pydist.get("size", 0),
            filename=pydist.get("filename", ""),
        )

    def release_facts(self, package: str) -> ReleaseFacts:
        """The release facts the trust panel shows, or why they are unknown.

        One request, no cache: the panel is asked for rarely (a selection, a
        pending install), and a stale "first released" date would defeat the
        one thing it exists to catch - a package published minutes ago.
        """
        name = validate_package_name(package)
        url = f"{self._json_base}/{name}/json"

        try:
            payload = self._http.get_json(url, max_bytes=4 * 1024 * 1024)
        except (HttpError, ResponseTooLargeError) as error:
            return ReleaseFacts(name=name, error=str(error))

        return self._parse_release_facts(name, payload)

    @staticmethod
    def _parse_release_facts(name: str, payload: Any) -> ReleaseFacts:
        """Read first/latest release dates and the wheel question out of the
        per-version file listings the JSON API returns."""
        releases = payload.get("releases") if isinstance(payload, Mapping) else None
        info = payload.get("info") if isinstance(payload, Mapping) else None
        info = info if isinstance(info, Mapping) else {}

        first_date = ""
        latest_date = ""
        latest_has_wheel = False
        counted = 0

        if isinstance(releases, Mapping):
            for files in releases.values():
                if not isinstance(files, list) or not files:
                    continue

                version_date = ""
                version_has_wheel = False
                for entry in files:
                    if not isinstance(entry, Mapping):
                        continue
                    stamp = str(
                        entry.get("upload_time_iso_8601") or entry.get("upload_time") or ""
                    )[:10]
                    if stamp and (not version_date or stamp < version_date):
                        version_date = stamp
                    if (
                        str(entry.get("filename", "")).endswith(".whl")
                        or str(entry.get("packagetype", "")) == "bdist_wheel"
                    ):
                        version_has_wheel = True

                if not version_date:
                    continue
                counted += 1
                if not first_date or version_date < first_date:
                    first_date = version_date
                if version_date > latest_date:
                    latest_date = version_date
                    latest_has_wheel = version_has_wheel

        homepage = str(info.get("home_page") or "").strip()
        if not homepage and isinstance(info.get("project_urls"), Mapping):
            for value in info["project_urls"].values():
                candidate = str(value or "").strip()
                if candidate.startswith(("http://", "https://")):
                    homepage = candidate
                    break

        return ReleaseFacts(
            name=name,
            first_release=first_date,
            releases=counted,
            latest_release=latest_date,
            latest_has_wheel=latest_has_wheel,
            homepage=homepage,
            license=str(info.get("license") or "").strip(),
        )

    # ------------------------------------------------------------------
    # Internal network paths
    # ------------------------------------------------------------------

    def _fetch_info(self, name: str) -> PackageInfo:
        url = f"{self._json_base}/{name}/json"

        try:
            payload = self._http.get_json(url, max_bytes=4 * 1024 * 1024)
        except ResponseTooLargeError:
            return self._failed_info(name, "response too large", fetched=True)
        except HttpError as error:
            # Being offline is an expected state, not a misconfiguration, and the
            # UI says so differently: nothing was fetched, so it can retry later
            # without implying the index or the URL is wrong.
            if is_offline_error(error):
                return self._failed_info(name, "offline", fetched=False)
            return self._failed_info(name, str(error), fetched=True)

        info = self._parse_json_info(name, payload)
        info.last_checked = monotonic()
        info.fetched = True
        # A response that parsed into an error is not an answer: caching it
        # would serve the same failure for the whole TTL instead of retrying.
        if not info.error:
            self._cache.set(info)
        return info

    @staticmethod
    def _failed_info(name: str, reason: str, *, fetched: bool) -> PackageInfo:
        """A cached-looking answer for a lookup that could not be completed."""
        return PackageInfo(
            name=name,
            canonical_name=name,
            latest_version="",
            last_checked=monotonic(),
            fetched=fetched,
            error=reason,
        )

    def _fetch_versions(self, name: str) -> tuple[str, ...]:
        url = f"{self._json_base}/{name}/json"

        try:
            payload = self._http.get_json(url, max_bytes=4 * 1024 * 1024)
        except (HttpError, ResponseTooLargeError):
            return ()

        # The settings cap how many versions are worth carrying around; the
        # picker only shows the newest ones anyway.
        return tuple(sorted(self._parse_json_versions(payload), reverse=True)[: self._max_versions])

    def _fetch_distributions(self, name: str, version: str) -> dict[str, Any]:
        url = f"{self._json_base}/{name}/{version}/json"

        try:
            payload = self._http.get_json(url, max_bytes=4 * 1024 * 1024)
        except (HttpError, ResponseTooLargeError):
            return {}

        if not isinstance(payload, dict):
            return {}

        return payload

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_json_info(self, name: str, payload: Any) -> PackageInfo:
        if not isinstance(payload, dict):
            return PackageInfo(
                name=name,
                canonical_name=name,
                latest_version="",
                error="unexpected response shape",
            )

        info_section = payload.get("info")
        if not isinstance(info_section, dict):
            return PackageInfo(
                name=name,
                canonical_name=name,
                latest_version="",
                error="missing info section",
            )

        version = str(payload.get("version") or "")
        latest = _latest_version(version, payload)
        summary = str(info_section.get("summary") or "").strip()
        home_page = str(info_section.get("home_page") or "").strip()
        project_url = str(info_section.get("project_url") or "").strip()
        requires_python = str(info_section.get("requires_python") or "").strip()
        classifiers = tuple(
            str(c) for c in (info_section.get("classifiers") or []) if isinstance(c, str)
        )

        return PackageInfo(
            name=name,
            canonical_name=name,
            latest_version=latest,
            summary=summary,
            home_page=home_page,
            project_url=project_url,
            requires_python=requires_python,
            classifiers=classifiers,
        )

    def _parse_json_versions(self, payload: Any) -> list[str]:
        releases = payload.get("releases")
        if not isinstance(releases, Mapping):
            return []

        versions: list[str] = []
        for raw_version, files in releases.items():
            try:
                from packaging.version import InvalidVersion as PackagingInvalidVersion
                from packaging.version import Version as PackagingVersion

                PackagingVersion(raw_version)
            except PackagingInvalidVersion:
                continue
            if files:
                versions.append(raw_version)

        return sorted(versions, reverse=True)


def _is_prerelease(version: str) -> bool:
    """True when *version* is an rc/beta/dev that an ordinary upgrade skips.

    ``pip install --upgrade`` and ``uv pip install --upgrade`` never move to a
    pre-release unless ``--pre`` is passed, so a pre-release is not a version
    any upgrade this application offers can reach.
    """
    if not version:
        return False

    from packaging.version import InvalidVersion, Version

    try:
        return Version(version).is_prerelease
    except InvalidVersion:
        return False


def _release_files(files: Any) -> list[Any]:
    """The files of one release that pip would actually consider installing.

    A release with no files, or whose every file is yanked, is not a candidate:
    the resolver skips it, so "latest" must skip it too - a yanked or empty
    release is the same dead end a pre-release is.
    """
    if not isinstance(files, list):
        return []

    usable = [entry for entry in files if isinstance(entry, dict) and not entry.get("yanked")]
    return usable


def _latest_version(version: str, payload: Any) -> str:
    """The version an ordinary ``pip install --upgrade`` would move to.

    PyPI's JSON document reports its newest *upload* at the top level -
    pre-release included - so taking that field at face value is how a package
    sits "outdated" forever: the badge points at ``3.1.0rc0`` while the upgrade
    command - correctly - installs nothing, and the row never turns green no
    matter how many times the user upgrades.

    The answer is therefore picked from the releases the way the resolver
    picks candidates: the newest **stable** release that has at least one
    un-yanked file. Only when a project has published nothing but pre-releases
    is the newest of those returned; a project that only ever published
    pre-releases has no "latest" an upgrade could reach.
    """
    releases = payload.get("releases")
    if not isinstance(releases, Mapping):
        # No list to reason over, so the top-level version is all there is.
        # Believe it only when it is something an upgrade could install.
        return "" if _is_prerelease(version) else version

    from packaging.version import InvalidVersion, Version

    stable: list[Version] = []
    any_release: list[Version] = []
    for raw_version, files in releases.items():
        try:
            parsed = Version(raw_version)
        except InvalidVersion:
            continue
        if not _release_files(files):
            continue
        any_release.append(parsed)
        if not parsed.is_prerelease:
            stable.append(parsed)

    if stable:
        return str(max(stable))
    if any_release:
        return str(max(any_release))

    # Nothing with a usable file at all; the top-level version is the last
    # resort, and only when it is not a dead end itself.
    return "" if _is_prerelease(version) else version


def pypi_info(package: str, *, http: HttpClient | None = None) -> PackageInfo:
    """Convenience wrapper for a one-off PyPI lookup with a default client."""
    with PyPIClient(http=http) as client:
        return client.info(package)
