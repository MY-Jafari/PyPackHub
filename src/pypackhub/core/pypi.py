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
    "pypi_info",
]


PYPI_JSON_BASE: Final = "https://pypi.org/pypi"
PYPI_SIMPLE_BASE: Final = "https://pypi.org/simple"


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


class PyPICache:
    """A tiny in-memory cache for PyPI lookups.

    This is the section 2 skeleton. It is deliberately simple: package name to
    info, with an age check. A later section adds a disk cache and eviction
    policy.
    """

    def __init__(self, *, ttl: float = 300.0) -> None:
        self._ttl = ttl
        self._store: dict[str, PackageInfo] = {}

    def get(self, name: str) -> PackageInfo | None:
        info = self._store.get(name)
        if info is None:
            return None
        if monotonic() - info.last_checked > self._ttl:
            return None
        return info

    def set(self, info: PackageInfo) -> None:
        self._store[info.name] = info

    def clear(self) -> None:
        self._store.clear()


class PyPIClient:
    """PyPI client with an HTTPS-only HTTP layer and a small TTL cache."""

    def __init__(
        self,
        *,
        http: HttpClient | None = None,
        cache: PyPICache | None = None,
        ttl: float | None = None,
    ) -> None:
        self._http = http or HttpClient(allow_insecure_http=False)
        self._cache = cache or PyPICache(ttl=ttl if ttl is not None else 300.0)

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

    # ------------------------------------------------------------------
    # Internal network paths
    # ------------------------------------------------------------------

    def _fetch_info(self, name: str) -> PackageInfo:
        url = f"{PYPI_JSON_BASE}/{name}/json"

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
        url = f"{PYPI_JSON_BASE}/{name}/json"

        try:
            payload = self._http.get_json(url, max_bytes=4 * 1024 * 1024)
        except (HttpError, ResponseTooLargeError):
            return ()

        return tuple(sorted(self._parse_json_versions(payload), reverse=True))

    def _fetch_distributions(self, name: str, version: str) -> dict[str, Any]:
        url = f"{PYPI_JSON_BASE}/{name}/{version}/json"

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
