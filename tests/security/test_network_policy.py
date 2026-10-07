"""SPEC section 11.6: HTTPS only, with certificate validation that cannot be turned off.

The client is exercised through an injected transport, so these tests never
touch the network - which also means they run identically offline.
"""

from __future__ import annotations

import inspect as inspect_module
from typing import Any

import httpx
import pytest

from pypackhub.core.net import (
    HttpClient,
    HttpError,
    InsecureUrlError,
    MalformedResponseError,
    ResponseTooLargeError,
    UnsafeUrlError,
    is_offline_error,
    validate_request_url,
)

pytestmark = pytest.mark.security


def serving(payload: Any, *, status: int = 200) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, json=payload))


def test_https_urls_are_accepted() -> None:
    url = "https://pypi.org/pypi/requests/json"

    assert validate_request_url(url) == url


def test_plain_http_is_refused() -> None:
    with pytest.raises(InsecureUrlError):
        validate_request_url("http://pypi.org/simple")


def test_plain_http_requires_an_explicit_opt_in() -> None:
    url = "http://mirror.internal/simple"

    assert validate_request_url(url, allow_insecure_http=True) == url


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.org/x",
        "https://",
        "pypi.org/simple",
        "",
    ],
)
def test_urls_that_are_not_absolute_https_are_refused(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        validate_request_url(url)


def test_credentials_inside_a_url_are_refused() -> None:
    with pytest.raises(UnsafeUrlError) as info:
        validate_request_url("https://user:password@mirror.example/simple")

    assert "credentials" in str(info.value)


def test_there_is_no_way_to_disable_certificate_verification() -> None:
    # The strongest possible form of this guarantee: the parameter that would
    # weaken it does not exist, so no call site can pass it.
    parameters = inspect_module.signature(HttpClient).parameters

    assert "verify" not in parameters


def test_json_is_fetched_and_parsed() -> None:
    client = HttpClient(transport=serving({"info": {"version": "2.31.0"}}))

    try:
        assert client.get_json("https://pypi.org/pypi/requests/json") == {
            "info": {"version": "2.31.0"}
        }
    finally:
        client.close()


def test_an_oversized_response_is_abandoned() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 8192))
    client = HttpClient(transport=transport, max_bytes=512)

    try:
        with pytest.raises(ResponseTooLargeError):
            client.get_text("https://example.org/huge")
    finally:
        client.close()


def test_an_error_status_is_reported_with_the_code() -> None:
    client = HttpClient(transport=serving({"error": "nope"}, status=503))

    try:
        with pytest.raises(HttpError) as info:
            client.get_text("https://example.org/down")
    finally:
        client.close()

    assert "503" in str(info.value)


def test_malformed_json_is_reported_rather_than_leaking_a_decode_error() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"{not json at all")
    )
    client = HttpClient(transport=transport)

    try:
        with pytest.raises(MalformedResponseError):
            client.get_json("https://example.org/broken")
    finally:
        client.close()


def test_a_redirect_to_plain_http_is_stopped_before_it_is_sent() -> None:
    requested: list[str] = []

    def route(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))

        return httpx.Response(302, headers={"location": "http://evil.example/payload"})

    client = HttpClient(transport=httpx.MockTransport(route))

    try:
        with pytest.raises(InsecureUrlError):
            client.get_json("https://mirror.example/simple")
    finally:
        client.close()

    # The downgraded request never reached the transport at all.
    assert requested == ["https://mirror.example/simple"]


def test_offline_failures_are_recognised() -> None:
    assert is_offline_error(httpx.ConnectError("no route to host")) is True
    assert is_offline_error(httpx.ConnectTimeout("timed out")) is True
    assert is_offline_error(ValueError("something else")) is False

    wrapped = HttpError("GET https://example.org failed")
    wrapped.__cause__ = httpx.ReadTimeout("timed out")

    assert is_offline_error(wrapped) is True


def test_a_connection_failure_becomes_an_http_error() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    client = HttpClient(transport=httpx.MockTransport(explode))

    try:
        with pytest.raises(HttpError) as info:
            client.get_text("https://example.org/x")
    finally:
        client.close()

    assert is_offline_error(info.value) is True
