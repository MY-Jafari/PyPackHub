"""Vulnerability scanning against the OSV.dev API.

SPEC section 11.4 shapes every decision here:

* **What is sent**: one ``name`` and one ``version`` per request, nothing
  else - no paths, no hostnames, no usernames. The request body is built in
  this module from those two strings and nowhere else.
* **HTTPS only**, through the hardened client in :mod:`pypackhub.core.net`,
  with timeouts and a response-size cap.
* **Untrusted answers**: OSV's response is parsed defensively. An advisory
  without a fix version, without a summary or without a usable link is shown
  with what it does have; a malformed one is skipped, never crashed on.
* **Opt-in**: nothing here runs on its own. The scan starts when the user
  presses the scan button and reports the timestamp of the last run so a
  result can be recognised as stale.

The "fixed in" version is the highest ``fixed`` event across the ranges that
apply to the installed version - the version an upgrade should move to.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, Final

from packaging.version import InvalidVersion, Version

from pypackhub.core.net import HttpClient, HttpError, is_offline_error

__all__ = [
    "OsvClient",
    "ScanReport",
    "Vulnerability",
]

#: The OSV query endpoint. HTTPS only; the URL is a constant, not user input.
OSV_QUERY_URL: Final = "https://api.osv.dev/v1/query"

#: The page an advisory is shown on, by advisory id.
ADVISORY_URL: Final = "https://osv.dev/vulnerability/{id}"

#: How many packages are asked about at once. A 300-package environment is
#: 300 small POSTs; eight at a time keeps that a few seconds.
SCAN_WORKERS: Final = 8

#: One advisory's answer is a page of JSON, but a hostile server is a hostile
#: server: the cap stays.
MAX_ADVISORY_BYTES: Final = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Vulnerability:
    """One advisory that applies to one installed version."""

    package: str
    installed_version: str
    advisory_id: str
    summary: str = ""
    url: str = ""
    #: The highest version the advisory says the issue is fixed in, when it
    #: says so at all. Empty when no fix is published yet.
    fixed_in: str = ""
    #: OSV/GHSA severity word (``LOW``/``MODERATE``/``HIGH``/``CRITICAL``)
    #: lower-cased, or empty when the advisory does not say.
    severity: str = ""

    @property
    def is_fixable(self) -> bool:
        """True when a fixed version is published for this advisory."""
        return bool(self.fixed_in)


@dataclass(frozen=True, slots=True)
class ScanReport:
    """What one scan of one environment found."""

    #: ``(package, installed_version)`` pairs that were asked about.
    scanned: tuple[tuple[str, str], ...] = ()
    findings: tuple[Vulnerability, ...] = ()
    #: Packages whose lookup failed, as ``(name, error)``. A single unreachable
    #: advisory must not take the scan down with it.
    errors: tuple[tuple[str, str], ...] = ()
    offline: bool = False
    timestamp: float = 0.0

    @property
    def vulnerable_packages(self) -> tuple[str, ...]:
        """The affected package names, in first-seen order."""
        seen: list[str] = []
        for finding in self.findings:
            if finding.package not in seen:
                seen.append(finding.package)
        return tuple(seen)


class OsvClient:
    """Ask OSV.dev about installed versions, one package at a time."""

    def __init__(self, *, http: HttpClient | None = None) -> None:
        self._http = http or HttpClient(allow_insecure_http=False)

    def scan(
        self,
        pairs: Sequence[tuple[str, str]],
        *,
        cancel: threading.Event | None = None,
        on_finding: Callable[[Vulnerability], None] | None = None,
    ) -> ScanReport:
        """Scan ``(name, version)`` pairs and report every matching advisory.

        Lookups run several at a time and findings are handed to ``on_finding``
        as they arrive (from a worker thread), so the page can fill in instead
        of waiting for the last answer. Cancellation stops *asking*.
        """
        scanned = tuple(pairs)
        findings: list[Vulnerability] = []
        errors: list[tuple[str, str]] = []
        offline = False

        if not scanned:
            return ScanReport(timestamp=time.time())

        workers = min(SCAN_WORKERS, len(scanned))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pypackhub-osv") as pool:
            queue = iter(scanned)
            pending: dict[Future[list[Vulnerability]], str] = {}

            while True:
                while len(pending) < workers and not _is_cancelled(cancel):
                    try:
                        name, version = next(queue)
                    except StopIteration:
                        break
                    pending[pool.submit(self._scan_one, name, version)] = name

                if not pending:
                    break

                # Cancellation stops *asking*: in-flight requests answer, but
                # no new ones go out once the user has pressed stop.
                done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
                for future in done:
                    name = pending.pop(future)
                    try:
                        for finding in future.result():
                            findings.append(finding)
                            if on_finding is not None:
                                on_finding(finding)
                    except HttpError as error:
                        if is_offline_error(error):
                            offline = True
                        errors.append((name, str(error)))
                    except Exception as error:
                        errors.append((name, str(error)))

        return ScanReport(
            scanned=scanned,
            findings=tuple(findings),
            errors=tuple(errors),
            offline=offline,
            timestamp=time.time(),
        )

    def _scan_one(self, name: str, version: str) -> list[Vulnerability]:
        """Ask OSV about one package, returning every advisory that applies."""
        payload = {
            "package": {"name": name, "ecosystem": "PyPI"},
            "version": version,
        }
        answer = self._http.post_json(OSV_QUERY_URL, payload, max_bytes=MAX_ADVISORY_BYTES)

        findings: list[Vulnerability] = []
        if not isinstance(answer, dict):
            return findings

        raw_advisories = answer.get("vulns")
        if not isinstance(raw_advisories, list):
            return findings

        for raw in raw_advisories:
            parsed = _parse_advisory(raw, name, version)
            if parsed is not None:
                findings.append(parsed)

        return findings


def _parse_advisory(raw: Any, package: str, installed_version: str) -> Vulnerability | None:
    """One OSV advisory record, or ``None`` when it is not usable.

    The response is untrusted input from another server: every field is read
    defensively, and an advisory without an id is dropped rather than shown.
    """
    if not isinstance(raw, dict):
        return None

    advisory_id = str(raw.get("id") or "").strip()
    if not advisory_id:
        return None

    summary = str(raw.get("summary") or "").strip()

    url = ""
    for reference in raw.get("references") or []:
        if isinstance(reference, dict) and str(reference.get("type") or "") == "WEB":
            candidate = str(reference.get("url") or "")
            if candidate.startswith("https://"):
                url = candidate
                break
    if not url:
        url = ADVISORY_URL.format(id=advisory_id)

    severity = ""
    database_specific = raw.get("database_specific")
    if isinstance(database_specific, dict):
        severity = str(database_specific.get("severity") or "").strip().lower()
    if not severity:
        ecosystem_specific = raw.get("ecosystem_specific")
        if isinstance(ecosystem_specific, dict):
            severity = str(ecosystem_specific.get("severity") or "").strip().lower()

    fixed_in = _fixed_in(raw, installed_version)

    return Vulnerability(
        package=package,
        installed_version=installed_version,
        advisory_id=advisory_id,
        summary=summary,
        url=url,
        fixed_in=fixed_in,
        severity=severity,
    )


def _fixed_in(raw: Any, installed_version: str) -> str:
    """The highest published fix across the ranges that cover the install.

    OSV reports ``affected`` entries with version ranges; the ranges that
    actually contain the installed version are the ones that matter, and the
    answer is the highest ``fixed`` event among them.
    """
    affected_entries = raw.get("affected")
    if not isinstance(affected_entries, list):
        return ""

    try:
        installed = Version(installed_version)
    except InvalidVersion:
        # An unparseable installed version cannot be matched against ranges;
        # the advisory is still shown, without a fix promise.
        return ""

    fixes: list[Version] = []
    for entry in affected_entries:
        if not isinstance(entry, dict):
            continue

        package = entry.get("package")
        if isinstance(package, dict) and str(package.get("ecosystem") or "") not in {"PyPI", ""}:
            continue

        for fixed in _fixes_covering(entry.get("ranges"), installed):
            if fixed is not None:
                fixes.append(fixed)

    return str(max(fixes)) if fixes else ""


def _fixes_covering(ranges: Any, installed: Version) -> list[Version | None]:
    """``fixed`` events from every ECOSYSTEM-style range containing *installed*.

    A range with no ``introduced`` event (or one of OSV's "0" sentinels)
    covers everything up to its fix. A range the installed version is outside
    contributes nothing: the advisory does not apply through it.
    """
    events: list[Version | None] = []
    if not isinstance(ranges, list):
        return events

    for item in ranges:
        if not isinstance(item, dict):
            continue

        raw_events = item.get("events")
        if not isinstance(raw_events, list):
            continue

        introduced: Version | None = None
        fixed: Version | None = None

        for raw_event in raw_events:
            if not isinstance(raw_event, dict):
                continue

            candidate = _parse_version(raw_event.get("introduced"))
            if candidate is not None:
                introduced = candidate
            candidate = _parse_version(raw_event.get("fixed"))
            if candidate is not None:
                fixed = candidate

        if introduced is None or installed >= introduced:
            events.append(fixed)

    return events


def _parse_version(value: Any) -> Version | None:
    """A version from advisory data, or ``None`` when it is not one.

    Advisory text is untrusted: an unparsable version means "no range", never
    a crash and never a guess.
    """
    if value is None:
        return None

    try:
        return Version(str(value))
    except InvalidVersion:
        return None


def _is_cancelled(cancel: threading.Event | None) -> bool:
    return cancel is not None and cancel.is_set()
