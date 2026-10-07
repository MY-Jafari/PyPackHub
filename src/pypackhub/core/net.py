"""The only way PyPackHub talks to the network.

Every rule from SPEC section 11.6 is enforced here rather than at each call
site, because a call site can forget:

* **HTTPS only.** A plain-``http`` URL is refused. That includes a redirect: a
  transport wrapper checks every request the way out, so a mirror that answers
  with a redirect to ``http://`` is stopped *before* the downgraded request is
  sent, not detected afterwards.
* **Certificate validation is always on.** There is deliberately no ``verify``
  parameter anywhere in this module. Weakening it would mean editing this file,
  which is the point.
* **Timeouts.** Connect and read timeouts are set on the client, not left to a
  caller to remember.
* **Size limits.** Responses are streamed and abandoned once they exceed the
  limit, so a hostile or broken server cannot exhaust the application's memory.
* **Safe JSON.** Parsing failures become :class:`MalformedResponseError` instead
  of leaking a ``JSONDecodeError`` from deep inside a worker thread.

Offline is a normal condition, not an error to apologise for:
:func:`is_offline_error` lets callers fall back to a cache quietly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import urlsplit

import httpx

from pypackhub import __version__
from pypackhub.core.secrets import redact

__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_TIMEOUT",
    "HttpClient",
    "HttpError",
    "InsecureUrlError",
    "MalformedResponseError",
    "ResponseTooLargeError",
    "UnsafeUrlError",
    "is_offline_error",
    "validate_request_url",
]

#: Seconds. Short enough that a dead host does not freeze a worker, long enough
#: for a slow but real index.
DEFAULT_TIMEOUT: Final = 15.0

#: Bytes. The largest honest PyPI JSON document is well under a megabyte; this
#: leaves room for growth without letting a response grow without bound.
DEFAULT_MAX_BYTES: Final = 4 * 1024 * 1024

#: Chunk size used while streaming a response body.
_CHUNK_SIZE: Final = 64 * 1024

USER_AGENT: Final = f"PyPackHub/{__version__} (+https://github.com/MY-Jafari/PyPackHub)"


class HttpError(RuntimeError):
    """Base class for every failure raised by this module."""


class UnsafeUrlError(HttpError):
    """The URL itself is not something we are willing to request."""


class InsecureUrlError(UnsafeUrlError):
    """The URL is not, or did not stay, HTTPS."""


class ResponseTooLargeError(HttpError):
    """The response exceeded the configured limit."""


class MalformedResponseError(HttpError):
    """The response arrived but could not be understood."""


def validate_request_url(url: str, *, allow_insecure_http: bool = False) -> str:
    """Return the URL if it is safe to request, or refuse it.

    ``allow_insecure_http`` exists for the one case the spec allows: a private
    mirror the user has been warned about, repeatedly, and acknowledged. It is
    never set from configuration without that acknowledgement.
    """

    candidate = url.strip()

    if not candidate:
        raise UnsafeUrlError("the URL is empty")

    parts = urlsplit(candidate)

    if not parts.scheme or not parts.hostname:
        raise UnsafeUrlError(f"{candidate!r} is not an absolute URL")

    scheme = parts.scheme.lower()

    if scheme != "https" and not (scheme == "http" and allow_insecure_http):
        raise InsecureUrlError(
            f"{candidate!r} is not HTTPS; PyPackHub only talks to indexes and APIs over HTTPS"
        )

    if parts.username or parts.password:
        raise UnsafeUrlError(
            f"{candidate!r} carries credentials; store them in the system keychain instead"
        )

    return candidate


class _PolicyTransport(httpx.BaseTransport):
    """Refuse a request before it leaves the machine.

    ``follow_redirects=True`` is the convenient setting, but it means the URL
    the application decided to trust is not necessarily the URL the socket ends
    up talking to. Wrapping the transport is what closes that gap.
    """

    def __init__(self, inner: httpx.BaseTransport, *, allow_insecure_http: bool) -> None:
        self._inner = inner
        self._allow_insecure_http = allow_insecure_http

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        validate_request_url(str(request.url), allow_insecure_http=self._allow_insecure_http)

        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


@dataclass
class HttpClient:
    """A small, policy-enforcing HTTP client.

    Create one per worker and close it, or use it as a context manager. The
    underlying ``httpx.Client`` is private on purpose: callers are meant to use
    :meth:`get_json` and friends rather than reach for the raw client and opt
    out of the rules above.
    """

    timeout: float = DEFAULT_TIMEOUT
    max_bytes: int = DEFAULT_MAX_BYTES
    allow_insecure_http: bool = False
    transport: httpx.BaseTransport | None = None
    headers: dict[str, str] = field(default_factory=dict)

    _client: httpx.Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        request_headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}
        request_headers.update(self.headers)

        inner = self.transport if self.transport is not None else httpx.HTTPTransport()

        self._client = httpx.Client(
            timeout=httpx.Timeout(self.timeout, connect=min(5.0, self.timeout)),
            follow_redirects=True,
            headers=request_headers,
            transport=_PolicyTransport(inner, allow_insecure_http=self.allow_insecure_http),
            # No `verify=False` escape hatch: it is not a parameter, so no call
            # site can pass it.
        )

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get_bytes(self, url: str, *, max_bytes: int | None = None) -> bytes:
        """Fetch a URL and return its body, refusing to buffer more than the limit."""

        safe_url = validate_request_url(url, allow_insecure_http=self.allow_insecure_http)
        limit = self.max_bytes if max_bytes is None else max_bytes
        collected = bytearray()

        try:
            with self._client.stream("GET", safe_url) as response:
                if response.status_code >= httpx.codes.BAD_REQUEST:
                    raise HttpError(f"GET {redact(safe_url)} returned HTTP {response.status_code}")

                for chunk in response.iter_bytes(_CHUNK_SIZE):
                    collected.extend(chunk)
                    if len(collected) > limit:
                        raise ResponseTooLargeError(
                            f"GET {redact(safe_url)} exceeded the {limit} byte response limit"
                        )
        except httpx.HTTPError as error:
            raise HttpError(f"GET {redact(safe_url)} failed: {redact(str(error))}") from error

        return bytes(collected)

    def get_text(self, url: str, *, max_bytes: int | None = None) -> str:
        """Fetch a URL and decode it as UTF-8, replacing anything invalid."""

        return self.get_bytes(url, max_bytes=max_bytes).decode("utf-8", errors="replace")

    def get_json(self, url: str, *, max_bytes: int | None = None) -> Any:
        """Fetch a URL and parse it as JSON."""

        payload = self.get_text(url, max_bytes=max_bytes)

        try:
            return json.loads(payload)
        except ValueError as error:
            raise MalformedResponseError(
                f"GET {redact(url)} did not return valid JSON: {redact(str(error))}"
            ) from error


#: Exception types that mean "the network is not there right now" rather than
#: "the request was wrong". Callers fall back to a cache and stay quiet.
_OFFLINE_TYPES: Final = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)


def is_offline_error(error: BaseException) -> bool:
    """True when a failure means "offline", not "misconfigured"."""

    if isinstance(error, _OFFLINE_TYPES):
        return True

    if isinstance(error, HttpError) and error.__cause__ is not None:
        return isinstance(error.__cause__, _OFFLINE_TYPES)

    return False
