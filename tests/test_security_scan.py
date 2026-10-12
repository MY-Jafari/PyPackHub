"""The OSV scan: what is sent, what is believed, and what survives bad data.

The client is exercised through an injected transport, exactly like the
network-policy suite - no test talks to api.osv.dev. The payloads mirror the
shape OSV actually answers with, including the holes real advisories have:
no fix version, no summary, unparsable versions, ranges that do not apply.
"""

from __future__ import annotations

import threading

import httpx

from pypackhub.core.net import HttpClient
from pypackhub.core.security import OsvClient


def serving(payload: object, *, status: int = 200) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, json=payload))


def client_for(payload: object) -> OsvClient:
    return OsvClient(http=HttpClient(transport=serving(payload)))


def advisory(
    advisory_id: str = "GHSA-1111-aaaa",
    *,
    fixed: str | None = "2.5.1",
    introduced: str = "0",
    severity: str | None = "HIGH",
    summary: str = "RCE in the widget parser",
) -> dict[str, object]:
    events: list[dict[str, str]] = [{"introduced": introduced}]
    if fixed is not None:
        events.append({"fixed": fixed})
    return {
        "id": advisory_id,
        "summary": summary,
        "database_specific": {"severity": severity} if severity else None,
        "references": [{"type": "WEB", "url": "https://example.com/a"}],
        "affected": [
            {"package": {"name": "widget", "ecosystem": "PyPI"}, "ranges": [{"events": events}]}
        ],
    }


def test_the_query_sends_only_name_and_version() -> None:
    """SPEC 11.4: name==version and nothing else - no paths, no hostnames."""
    captured: dict[str, object] = {}

    def record(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode("utf-8")
        return httpx.Response(200, json={})

    scanner = OsvClient(http=HttpClient(transport=httpx.MockTransport(record)))
    scanner.scan([("widget", "2.5.0")])

    assert (
        captured["body"]
        == '{"package": {"name": "widget", "ecosystem": "PyPI"}, "version": "2.5.0"}'
    )


def test_a_finding_carries_the_fix_and_the_severity() -> None:
    report = client_for({"vulns": [advisory()]}).scan([("widget", "2.5.0")])

    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.advisory_id == "GHSA-1111-aaaa"
    assert finding.fixed_in == "2.5.1"
    assert finding.severity == "high"
    assert finding.url == "https://example.com/a"
    assert finding.is_fixable


def test_an_advisory_with_no_fix_is_shown_but_not_fixable() -> None:
    report = client_for({"vulns": [advisory(fixed=None)]}).scan([("widget", "2.5.0")])

    assert report.findings[0].fixed_in == ""
    assert not report.findings[0].is_fixable


def test_a_range_that_does_not_apply_contributes_no_fix() -> None:
    """The installed version outside the range is not affected by it."""
    payload = {
        "vulns": [
            advisory(introduced="3.0.0", fixed="3.1.0"),  # covers 3.x only
        ]
    }

    report = client_for(payload).scan([("widget", "2.5.0")])

    assert report.findings[0].fixed_in == ""


def test_the_highest_applicable_fix_wins() -> None:
    payload = {
        "vulns": [
            advisory(advisory_id="GHSA-2222-bbbb", fixed="2.6.0"),
            advisory(advisory_id="GHSA-3333-cccc", fixed="2.7.0"),
        ]
    }

    findings = {
        finding.advisory_id: finding
        for finding in client_for(payload).scan([("widget", "2.5.0")]).findings
    }

    assert findings["GHSA-3333-cccc"].fixed_in == "2.7.0"


def test_junk_in_an_advisory_never_crashes_the_scan() -> None:
    """Advisory text is untrusted: holes degrade the row, never the scan."""
    payload = {
        "vulns": [
            "not-a-dict",
            {"no_id": True},
            {
                "id": "GHSA-4444-dddd",
                "affected": [{"ranges": [{"events": [{"fixed": "not a version"}]}]}],
                "references": "not-a-list",
            },
        ]
    }

    report = client_for(payload).scan([("widget", "2.5.0")])

    assert [finding.advisory_id for finding in report.findings] == ["GHSA-4444-dddd"]
    assert report.findings[0].fixed_in == ""


def test_one_unreachable_package_is_reported_not_fatal() -> None:
    def refusing(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    scanner = OsvClient(http=HttpClient(transport=httpx.MockTransport(refusing)))
    report = scanner.scan([("widget", "2.5.0")])

    assert not report.findings
    assert report.errors and "widget" in report.errors[0][0]


def test_an_empty_environment_scans_clean_and_stamped() -> None:
    report = client_for({}).scan([])

    assert report.findings == ()
    assert report.timestamp > 0.0


def test_cancellation_stops_asking() -> None:
    """A set cancel event means no lookups go out at all."""
    served = {"vulns": [advisory()]}
    requests: list[str] = []

    def counting(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, json=served)

    scanner = OsvClient(http=HttpClient(transport=httpx.MockTransport(counting)))
    cancel = threading.Event()
    cancel.set()

    report = scanner.scan([("widget", "2.5.0"), ("other", "1.0")], cancel=cancel)

    assert requests == []
    assert report.findings == ()
